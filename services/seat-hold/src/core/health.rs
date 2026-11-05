use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use serde::Serialize;
use utoipa::ToSchema;

use crate::app::AppState;
use crate::core::db::DbPool;

#[derive(Serialize, ToSchema)]
pub struct HealthOut {
    status: &'static str,
}

#[derive(Serialize, ToSchema)]
pub struct Checks {
    db: &'static str,
}

#[derive(Serialize, ToSchema)]
pub struct ReadyOut {
    checks: Checks,
}

#[utoipa::path(get, path = "/health", responses((status = 200, body = HealthOut)))]
pub async fn health() -> Json<HealthOut> {
    Json(HealthOut { status: "ok" })
}

async fn ping<D: DbPool>(db: &D) -> Result<(), sqlx::Error> {
    let mut conn = db.get_conn().await?;
    sqlx::query("SELECT 1").execute(&mut *conn).await?;
    Ok(())
}

#[utoipa::path(
    get,
    path = "/ready",
    responses(
        (status = 200, body = ReadyOut),
        (status = 503, description = "A dependency is down", body = ReadyOut),
    )
)]
pub async fn ready<D: DbPool>(State(state): State<AppState<D>>) -> (StatusCode, Json<ReadyOut>) {
    match ping(&state.db).await {
        Ok(()) => (StatusCode::OK, ready_out("ok")),
        Err(error) => {
            tracing::warn!(%error, "Readiness check failed: db unreachable");
            (StatusCode::SERVICE_UNAVAILABLE, ready_out("unreachable"))
        }
    }
}

fn ready_out(db: &'static str) -> Json<ReadyOut> {
    Json(ReadyOut {
        checks: Checks { db },
    })
}
