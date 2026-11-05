use axum::Router;
use axum::middleware::from_fn;
use axum::routing::get;

use crate::db::DbPool;
use crate::errors::ServiceError;
use crate::handlers::health;
use crate::trace_context::trace_request;

pub fn build_app<D: DbPool>(db: D) -> Router {
    Router::new()
        .route("/health", get(health::health))
        .route("/ready", get(health::ready::<D>))
        .fallback(async || ServiceError::NotFound)
        .layer(from_fn(trace_request))
        .with_state(db)
}
