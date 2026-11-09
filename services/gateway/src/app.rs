use std::sync::Arc;

use axum::extract::DefaultBodyLimit;
use axum::middleware::{from_fn, from_fn_with_state};
use axum::routing::{any, get};
use axum::{Extension, Router};
use reqwest::Client;
use reqwest::redirect::Policy;

use crate::auth::{TokenVerifier, authenticate};
use crate::errors::GatewayError;
use crate::handlers::{health, proxy};
use crate::limits::rate_limit::limiter::{self, RateLimiter};
use crate::settings::{ClientSettings, RetrySettings, Settings};
use crate::trace_context::trace_request;
use crate::upstream::{Upstream, UpstreamLimits};

#[derive(Clone)]
pub struct AppState {
    pub client: Client,
    pub retry: RetrySettings,
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
        protected = protected.route_layer(from_fn_with_state(Arc::new(limiter), limiter::limit));
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
