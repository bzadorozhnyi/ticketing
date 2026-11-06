use axum::Router;
use axum::routing::post;

use super::confirm_hold::confirm_hold;
use super::extend_hold::extend_hold;
use crate::app::AppState;
use crate::core::db::DbPool;

/// Not routed by the gateway: a client must not confirm a hold without paying.
pub fn router<D: DbPool>() -> Router<AppState<D>> {
    Router::new()
        .route("/{hold_id}/extend", post(extend_hold::<D>))
        .route("/{hold_id}/confirm", post(confirm_hold::<D>))
}
