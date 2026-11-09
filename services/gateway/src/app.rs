use std::sync::Arc;
use std::time::Duration;

use axum::extract::DefaultBodyLimit;
use axum::middleware::{from_fn, from_fn_with_state};
use axum::routing::{any, get};
use axum::{Extension, Router};
use reqwest::redirect::Policy;
use reqwest::{Client, Url};
use tokio::sync::Semaphore;

use crate::auth::{TokenVerifier, authenticate};
use crate::breaker::CircuitBreaker;
use crate::errors::GatewayError;
use crate::handlers::{health, proxy};
use crate::rate_limit::{self, RateLimiter};
use crate::retry::RetryBudget;
use crate::settings::{ClientSettings, RetrySettings, Settings};
use crate::trace_context::trace_request;

#[derive(Clone)]
pub struct AppState {
    pub client: Client,
    pub retry: RetrySettings,
}

/// One upstream with its own timeout, retry budget, circuit breaker and bulkhead:
/// a failing or slow catalog spends nothing of seat-hold's.
#[derive(Clone)]
pub struct Upstream {
    pub url: Url,
    pub timeout: Duration,
    pub budget: Arc<RetryBudget>,
    pub breaker: Arc<CircuitBreaker>,
    /// A permit per request in flight to this upstream, retries included.
    pub bulkhead: Arc<Semaphore>,
}

struct UpstreamLimits {
    timeout: Option<Duration>,
    max_concurrent: Option<usize>,
}

impl Upstream {
    fn new(name: &'static str, url: &Url, limits: &UpstreamLimits, settings: &Settings) -> Self {
        let max_concurrent = limits
            .max_concurrent
            .unwrap_or(settings.bulkhead.max_concurrent);
        Self {
            url: url.clone(),
            timeout: limits.timeout.unwrap_or(settings.client.request_timeout),
            budget: Arc::new(RetryBudget::new(&settings.retry)),
            breaker: Arc::new(CircuitBreaker::new(name, &settings.breaker)),
            bulkhead: Arc::new(Semaphore::new(max_concurrent)),
        }
    }
}

fn upstream_routes(prefix: &str, upstream: Upstream) -> Router<AppState> {
    Router::new()
        .route(prefix, any(proxy::forward))
        .route(&format!("{prefix}/{{*rest}}"), any(proxy::forward))
        .route_layer(Extension(upstream))
}

#[derive(Debug, thiserror::Error)]
pub enum BuildError {
    #[error(transparent)]
    Client(#[from] reqwest::Error),
    #[error(transparent)]
    RateLimit(#[from] redis::RedisError),
}

/// # Errors
/// An HTTP client that cannot be built, or an invalid Valkey URL.
pub fn build_app(settings: &Settings) -> Result<Router, BuildError> {
    let state = AppState {
        client: build_client(&settings.client)?,
        retry: settings.retry.clone(),
    };
    let verifier = TokenVerifier::new(&settings.auth.jwt_secret);

    // A token is the default: a public route is an explicit exception next to /health.
    let upstreams = &settings.upstreams;
    let catalog = Upstream::new(
        "catalog",
        &upstreams.catalog_url,
        &UpstreamLimits {
            timeout: upstreams.catalog_timeout,
            max_concurrent: upstreams.catalog_max_concurrent,
        },
        settings,
    );
    let seat_hold = Upstream::new(
        "seat-hold",
        &upstreams.seat_hold_url,
        &UpstreamLimits {
            timeout: upstreams.seat_hold_timeout,
            max_concurrent: upstreams.seat_hold_max_concurrent,
        },
        settings,
    );
    let booking = Upstream::new(
        "booking",
        &upstreams.booking_url,
        &UpstreamLimits {
            timeout: upstreams.booking_timeout,
            max_concurrent: upstreams.booking_max_concurrent,
        },
        settings,
    );
    let mut protected = upstream_routes("/api/v1/events", catalog)
        .merge(upstream_routes("/api/v1/holds", seat_hold))
        .merge(upstream_routes("/api/v1/orders", booking));
    // The last layer added runs first: the token is checked before the rate limit.
    if let Some(limiter) = RateLimiter::new(&settings.rate_limit, &settings.breaker)? {
        protected = protected.route_layer(from_fn_with_state(Arc::new(limiter), rate_limit::limit));
    }
    let protected = protected.route_layer(from_fn_with_state(verifier, authenticate));

    Ok(Router::new()
        .route("/health", get(health::health))
        .route("/ready", get(health::health))
        .merge(protected)
        .fallback(async || GatewayError::NotFound)
        .layer(DefaultBodyLimit::max(settings.limits.max_body_bytes))
        .layer(from_fn(trace_request))
        .with_state(state))
}

/// A proxy passes redirects to the client and ignores `HTTP_PROXY` from its environment.
/// The idle timeout stays below uvicorn's keep-alive (5s), so the gateway does not
/// reuse a connection the upstream is about to close.
fn build_client(settings: &ClientSettings) -> Result<Client, reqwest::Error> {
    Client::builder()
        .connect_timeout(settings.connect_timeout)
        .timeout(settings.request_timeout)
        .pool_idle_timeout(settings.pool_idle_timeout)
        .redirect(Policy::none())
        .no_proxy()
        .build()
}
