use axum::Router;
use axum::extract::DefaultBodyLimit;
use axum::middleware::from_fn;
use axum::routing::get;

use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::core::health;
use crate::core::settings::HoldSettings;
use crate::core::trace_context::trace_request;
use crate::holds::use_cases::hold_seats::HoldSeatsUseCase;
use crate::routes;

const MAX_BODY_BYTES: usize = 64 * 1024;

#[derive(Clone)]
pub struct AppState<D: DbPool> {
    pub db: D,
    pub hold_seats: HoldSeatsUseCase<D>,
}

pub fn build_app<D: DbPool>(db: D, holds: HoldSettings) -> Router {
    let hold_seats = HoldSeatsUseCase::new(db.clone(), holds);

    Router::new()
        .merge(routes::router::<D>())
        .route("/health", get(health::health))
        .route("/ready", get(health::ready::<D>))
        .fallback(async || ServiceError::NotFound)
        .layer(DefaultBodyLimit::max(MAX_BODY_BYTES))
        .layer(from_fn(trace_request))
        .with_state(AppState { db, hold_seats })
}
