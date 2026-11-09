use std::collections::HashMap;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use axum::extract::{Request, State};
use axum::middleware::Next;
use axum::response::Response;
use redis::aio::{ConnectionManager, ConnectionManagerConfig};
use redis::{RedisError, Script};
use tokio::time::Instant;

use crate::auth::AuthenticatedUser;
use crate::breaker::CircuitBreaker;
use crate::errors::GatewayError;
use crate::settings::{BreakerSettings, RateLimitSettings};

/// A token bucket per user, kept in Valkey so every gateway replica shares it. The
/// check and the update are one script: two replicas cannot both take the last token.
/// The time comes from Valkey, so the clocks of the replicas do not matter. Lua turns
/// fractions into integers on the way out, so the wait comes back in milliseconds.
const TOKEN_BUCKET: &str = r"
local time = redis.call('TIME')
local now = tonumber(time[1]) + tonumber(time[2]) / 1000000
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(state[1]) or burst
local at = tonumber(state[2]) or now
tokens = math.min(burst, tokens + math.max(0, now - at) * rate)
local allowed = 0
local wait_ms = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
else
    wait_ms = math.ceil((1 - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(burst / rate * 1000) + 1000)
return {allowed, wait_ms}
";

/// Users with a bucket in one replica's memory. The map is pruned when it is full; a
/// user over this many active ones is left to Valkey alone.
const MAX_LOCAL_USERS: usize = 100_000;

#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    Allowed,
    Limited { retry_after: Duration },
}

struct Bucket {
    tokens: f64,
    at: Instant,
}

struct LocalState {
    buckets: HashMap<String, Bucket>,
    next_prune: Instant,
}

/// The first tier: the same token bucket per user, in this replica's memory. It refuses
/// a user over the limit without a network call, so a flood never reaches Valkey, and
/// it keeps limiting while Valkey fails: then a user gets at most the limit times the
/// number of replicas.
pub struct LocalBuckets {
    per_second: f64,
    burst: f64,
    max_users: usize,
    state: Mutex<LocalState>,
}

impl LocalBuckets {
    #[must_use]
    pub fn new(per_second: f64, burst: u32, max_users: usize) -> Self {
        Self {
            per_second,
            burst: f64::from(burst),
            max_users,
            state: Mutex::new(LocalState {
                buckets: HashMap::new(),
                next_prune: Instant::now(),
            }),
        }
    }

    pub fn take(&self, user: &str, now: Instant) -> Decision {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(bucket) = state.buckets.get_mut(user) {
            return self.take_from(bucket, now);
        }
        if !self.make_room(&mut state, now) {
            return Decision::Allowed;
        }
        let mut bucket = Bucket {
            tokens: self.burst,
            at: now,
        };
        let decision = self.take_from(&mut bucket, now);
        state.buckets.insert(user.to_owned(), bucket);
        decision
    }

    fn take_from(&self, bucket: &mut Bucket, now: Instant) -> Decision {
        bucket.tokens = self.refilled(bucket, now);
        bucket.at = now;
        if bucket.tokens >= 1.0 {
            bucket.tokens -= 1.0;
            return Decision::Allowed;
        }
        let wait = (1.0 - bucket.tokens) / self.per_second;
        Decision::Limited {
            retry_after: Duration::try_from_secs_f64(wait).unwrap_or(Duration::MAX),
        }
    }

    fn refilled(&self, bucket: &Bucket, now: Instant) -> f64 {
        let elapsed = now.saturating_duration_since(bucket.at).as_secs_f64();
        (bucket.tokens + elapsed * self.per_second).min(self.burst)
    }

    /// A full bucket is the same as no bucket, so those go first. The map is scanned at
    /// most once per time an empty bucket takes to fill: many active users must not
    /// make every new one pay for a scan.
    fn make_room(&self, state: &mut LocalState, now: Instant) -> bool {
        if state.buckets.len() < self.max_users {
            return true;
        }
        if now >= state.next_prune {
            state
                .buckets
                .retain(|_, bucket| self.refilled(bucket, now) < self.burst);
            let fill_time = Duration::try_from_secs_f64(self.burst / self.per_second);
            state.next_prune = now + fill_time.unwrap_or(Duration::MAX);
        }
        state.buckets.len() < self.max_users
    }
}

/// Limits requests per user in two tiers: the replica's own buckets first, then the
/// bucket in Valkey shared by every replica. When Valkey is down or slow, the replica's
/// limit alone applies: a limit protects the system, it is not part of being correct,
/// and the entry must not fall with its counter. A breaker stops asking a Valkey that
/// hangs, so the requests do not each wait for its timeout.
pub struct RateLimiter {
    local: LocalBuckets,
    connection: ConnectionManager,
    script: Script,
    settings: RateLimitSettings,
    breaker: CircuitBreaker,
}

impl RateLimiter {
    /// `None` when no Valkey is configured. The connection opens with the first
    /// request, so the gateway starts while Valkey is down.
    ///
    /// # Errors
    /// An invalid Valkey URL.
    pub fn new(
        settings: &RateLimitSettings,
        breaker: &BreakerSettings,
    ) -> Result<Option<Self>, RedisError> {
        let Some(url) = &settings.valkey_url else {
            return Ok(None);
        };
        // Every command times out here, so a hanging Valkey costs a request at most this.
        let config = ConnectionManagerConfig::new()
            .set_connection_timeout(Some(settings.timeout))
            .set_response_timeout(Some(settings.timeout))
            .set_number_of_retries(1);
        let client = redis::Client::open(url.as_str())?;
        Ok(Some(Self {
            local: LocalBuckets::new(settings.per_second, settings.burst, MAX_LOCAL_USERS),
            connection: ConnectionManager::new_lazy_with_config(client, config)?,
            script: Script::new(TOKEN_BUCKET),
            settings: settings.clone(),
            breaker: CircuitBreaker::new("valkey", breaker),
        }))
    }

    pub async fn check(&self, user: &str) -> Decision {
        let local = self.local.take(user, Instant::now());
        if local != Decision::Allowed {
            return local;
        }
        let Ok(call) = self.breaker.try_call() else {
            return Decision::Allowed;
        };
        let mut connection = self.connection.clone();
        let result = self
            .script
            .key(format!("rate:{user}"))
            .arg(self.settings.per_second)
            .arg(self.settings.burst)
            .invoke_async::<(i64, i64)>(&mut connection)
            .await;
        match result {
            Ok((allowed, wait_ms)) => {
                call.finish(false);
                if allowed == 1 {
                    Decision::Allowed
                } else {
                    let wait = u64::try_from(wait_ms).unwrap_or(0);
                    Decision::Limited {
                        retry_after: Duration::from_millis(wait),
                    }
                }
            }
            Err(error) => {
                call.finish(true);
                tracing::warn!(error = %error, "Shared rate limit skipped: Valkey failed or was slow");
                Decision::Allowed
            }
        }
    }
}

/// Runs after authentication, before the retries, breakers and bulkheads: a refused
/// request takes no place of anyone else's.
pub async fn limit(
    State(limiter): State<Arc<RateLimiter>>,
    request: Request,
    next: Next,
) -> Result<Response, GatewayError> {
    let user = request
        .extensions()
        .get::<AuthenticatedUser>()
        .and_then(|user| user.0.to_str().ok())
        .map(str::to_owned);
    if let Some(user) = user
        && let Decision::Limited { retry_after } = limiter.check(&user).await
    {
        return Err(GatewayError::RateLimited { retry_after });
    }
    Ok(next.run(request).await)
}
