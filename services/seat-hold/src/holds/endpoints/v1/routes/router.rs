use axum::Router;
use axum::routing::post;

use super::create_hold::create_hold;
use crate::app::AppState;
use crate::core::db::DbPool;

pub fn router<D: DbPool>() -> Router<AppState<D>> {
    Router::new().route("/", post(create_hold::<D>))
}
