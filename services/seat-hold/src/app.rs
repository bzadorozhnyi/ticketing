use axum::Router;
use axum::extract::DefaultBodyLimit;
use axum::handler::Handler;
use axum::middleware::from_fn;
use axum::routing::get;

use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::core::health;
use crate::core::settings::HoldSettings;
use crate::core::telemetry::request::trace_request;
use crate::routes;

const MAX_BODY_BYTES: usize = 64 * 1024;

#[derive(Clone)]
pub struct AppState<D: DbPool> {
    pub db: D,
    pub holds: HoldSettings,
}

pub fn build_app<D: DbPool>(db: D, holds: HoldSettings) -> Router {
    Router::new()
        .merge(routes::router::<D>())
        .route("/health", get(health::health))
        .route("/ready", get(health::ready::<D>))
        // After routing, so the route template is known; the fallback gets the same layer.
        .route_layer(from_fn(trace_request))
        .fallback((async || ServiceError::NotFound).layer(from_fn(trace_request)))
        .layer(DefaultBodyLimit::max(MAX_BODY_BYTES))
        .with_state(AppState { db, holds })
}
