use axum::Router;

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::holds::endpoints::internal::routes::router::router as holds_internal_router;
use crate::holds::endpoints::v1::routes::router::router as holds_v1_router;

pub fn router<D: DbPool>() -> Router<AppState<D>> {
    Router::new()
        .nest("/api/v1/holds", holds_v1_router::<D>())
        .nest("/internal/v1/holds", holds_internal_router::<D>())
}
