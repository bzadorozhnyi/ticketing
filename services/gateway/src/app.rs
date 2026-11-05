use axum::extract::DefaultBodyLimit;
use axum::middleware::{from_fn, from_fn_with_state};
use axum::routing::{any, get};
use axum::{Extension, Router};
use reqwest::redirect::Policy;
use reqwest::{Client, Url};

use crate::auth::{TokenVerifier, authenticate};
use crate::errors::GatewayError;
use crate::handlers::{health, proxy};
use crate::settings::{ClientSettings, Settings};
use crate::trace_context::trace_request;

#[derive(Clone)]
pub struct AppState {
    pub client: Client,
}

#[derive(Clone)]
pub struct Upstream(pub Url);

fn upstream_routes(prefix: &str, url: &Url) -> Router<AppState> {
    Router::new()
        .route(prefix, any(proxy::forward))
        .route(&format!("{prefix}/{{*rest}}"), any(proxy::forward))
        .route_layer(Extension(Upstream(url.clone())))
}

pub fn build_app(settings: &Settings) -> Result<Router, reqwest::Error> {
    let state = AppState {
        client: build_client(&settings.client)?,
    };
    let verifier = TokenVerifier::new(&settings.auth.jwt_secret);

    // A token is the default: a public route is an explicit exception next to /health.
    let upstreams = &settings.upstreams;
    let protected = upstream_routes("/api/v1/events", &upstreams.catalog_url)
        .merge(upstream_routes("/api/v1/holds", &upstreams.seat_hold_url))
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
