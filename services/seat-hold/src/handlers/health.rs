use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use serde::Serialize;

use crate::db::DbPool;

#[derive(Serialize)]
pub struct HealthOut {
    status: &'static str,
}

#[derive(Serialize)]
pub struct Checks {
    db: &'static str,
}

#[derive(Serialize)]
pub struct ReadyOut {
    checks: Checks,
}

pub async fn health() -> Json<HealthOut> {
    Json(HealthOut { status: "ok" })
}

async fn ping<D: DbPool>(db: &D) -> Result<(), sqlx::Error> {
    let mut conn = db.get_conn().await?;
    sqlx::query("SELECT 1").execute(&mut *conn).await?;
    Ok(())
}

pub async fn ready<D: DbPool>(State(db): State<D>) -> (StatusCode, Json<ReadyOut>) {
    match ping(&db).await {
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
