use std::sync::Arc;
use std::time::Duration;

use axum::extract::DefaultBodyLimit;
use axum::middleware::{from_fn, from_fn_with_state};
use axum::routing::{any, get};
use axum::{Extension, Router};
use reqwest::redirect::Policy;
use reqwest::{Client, Url};

use crate::auth::{TokenVerifier, authenticate};
use crate::breaker::CircuitBreaker;
use crate::errors::GatewayError;
use crate::handlers::{health, proxy};
use crate::retry::RetryBudget;
use crate::settings::{ClientSettings, RetrySettings, Settings};
use crate::trace_context::trace_request;

#[derive(Clone)]
pub struct AppState {
    pub client: Client,
    pub retry: RetrySettings,
}

/// One upstream with its own timeout, retry budget and circuit breaker: a failing
/// catalog spends nothing of seat-hold's and does not open seat-hold's breaker.
#[derive(Clone)]
pub struct Upstream {
    pub url: Url,
    pub timeout: Duration,
    pub budget: Arc<RetryBudget>,
    pub breaker: Arc<CircuitBreaker>,
}

impl Upstream {
    fn new(name: &'static str, url: &Url, timeout: Option<Duration>, settings: &Settings) -> Self {
        Self {
            url: url.clone(),
            timeout: timeout.unwrap_or(settings.client.request_timeout),
            budget: Arc::new(RetryBudget::new(&settings.retry)),
            breaker: Arc::new(CircuitBreaker::new(name, &settings.breaker)),
        }
    }
}

fn upstream_routes(prefix: &str, upstream: Upstream) -> Router<AppState> {
    Router::new()
        .route(prefix, any(proxy::forward))
        .route(&format!("{prefix}/{{*rest}}"), any(proxy::forward))
        .route_layer(Extension(upstream))
}

pub fn build_app(settings: &Settings) -> Result<Router, reqwest::Error> {
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
        upstreams.catalog_timeout,
        settings,
    );
    let seat_hold = Upstream::new(
        "seat-hold",
        &upstreams.seat_hold_url,
        upstreams.seat_hold_timeout,
        settings,
    );
    let booking = Upstream::new(
        "booking",
        &upstreams.booking_url,
        upstreams.booking_timeout,
        settings,
    );
    let protected = upstream_routes("/api/v1/events", catalog)
        .merge(upstream_routes("/api/v1/holds", seat_hold))
        .merge(upstream_routes("/api/v1/orders", booking))
        .route_layer(from_fn_with_state(verifier, authenticate));

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
