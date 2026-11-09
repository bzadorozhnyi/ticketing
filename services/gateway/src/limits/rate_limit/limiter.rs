use std::sync::Arc;

use axum::extract::{Request, State};
use axum::middleware::Next;
use axum::response::Response;
use redis::RedisError;
use tokio::time::Instant;

use crate::auth::AuthenticatedUser;
use crate::errors::GatewayError;
use crate::limits::rate_limit::decision::Decision;
use crate::limits::rate_limit::local::LocalBuckets;
use crate::limits::rate_limit::shared::SharedBuckets;
use crate::settings::{BreakerSettings, RateLimitSettings};

/// Users with a bucket in one replica's memory. The map is pruned when it is full; a
/// user over this many active ones is left to Valkey alone.
const MAX_LOCAL_USERS: usize = 100_000;

/// Limits requests per user in two tiers: the replica's own buckets first, then the
/// bucket in Valkey shared by every replica. When Valkey is down or slow, the replica's
/// limit alone applies: a limit protects the system, it is not part of being correct,
/// and the entry must not fall with its counter.
pub struct RateLimiter {
    local: LocalBuckets,
    shared: SharedBuckets,
}

impl RateLimiter {
    /// `None` when no Valkey is configured.
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
        Ok(Some(Self {
            local: LocalBuckets::new(settings.per_second, settings.burst, MAX_LOCAL_USERS),
            shared: SharedBuckets::new(url, settings, breaker)?,
        }))
    }

    pub async fn check(&self, user: &str) -> Decision {
        let local = self.local.take(user, Instant::now());
        if local != Decision::Allowed {
            return local;
        }
        self.shared.take(user).await
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
