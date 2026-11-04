use axum::Router;
use axum::extract::DefaultBodyLimit;
use axum::middleware::from_fn;
use axum::routing::{any, get};
use reqwest::redirect::Policy;
use reqwest::{Client, Url};

use crate::errors::GatewayError;
use crate::handlers::{health, proxy};
use crate::settings::{ClientSettings, Settings};
use crate::trace_context::trace_request;

#[derive(Clone)]
pub struct AppState {
    pub client: Client,
    pub catalog_url: Url,
}

pub fn build_app(settings: &Settings) -> Result<Router, reqwest::Error> {
    let state = AppState {
        client: build_client(&settings.client)?,
        catalog_url: settings.upstreams.catalog_url.clone(),
    };
    Ok(Router::new()
        .route("/health", get(health::health))
        .route("/ready", get(health::health))
        .route("/api/v1/events", any(proxy::forward))
        .route("/api/v1/events/{*rest}", any(proxy::forward))
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
