use std::time::Duration;

use redis::aio::{ConnectionManager, ConnectionManagerConfig};
use redis::{RedisError, Script};

use crate::limits::rate_limit::decision::Decision;
use crate::resilience::breaker::CircuitBreaker;
use crate::settings::{BreakerSettings, RateLimitSettings};
use crate::telemetry::metrics::metrics;

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

/// The second tier: the bucket in Valkey, shared by every replica. It fails open: a
/// Valkey that is down or slower than the timeout allows the request, and the first
/// tier's limit is all that applies. A breaker stops asking a Valkey that hangs, so the
/// requests do not each wait for its timeout.
pub struct SharedBuckets {
    connection: ConnectionManager,
    script: Script,
    settings: RateLimitSettings,
    breaker: CircuitBreaker,
}

impl SharedBuckets {
    /// The connection opens with the first request, so the gateway starts while Valkey
    /// is down.
    ///
    /// # Errors
    /// An invalid Valkey URL.
    pub fn new(
        url: &str,
        settings: &RateLimitSettings,
        breaker: &BreakerSettings,
    ) -> Result<Self, RedisError> {
        // Every command times out here, so a hanging Valkey costs a request at most this.
        let config = ConnectionManagerConfig::new()
            .set_connection_timeout(Some(settings.timeout))
            .set_response_timeout(Some(settings.timeout))
            .set_number_of_retries(1);
        let client = redis::Client::open(url)?;
        Ok(Self {
            connection: ConnectionManager::new_lazy_with_config(client, config)?,
            script: Script::new(TOKEN_BUCKET),
            settings: settings.clone(),
            breaker: CircuitBreaker::new("valkey", breaker),
        })
    }

    pub async fn take(&self, user: &str) -> Decision {
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
                metrics().shared_rate_limit_skipped.add(1, &[]);
                Decision::Allowed
            }
        }
    }
}
