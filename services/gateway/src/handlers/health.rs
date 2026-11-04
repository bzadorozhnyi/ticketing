use axum::Json;
use serde::Serialize;

#[derive(Serialize)]
pub struct HealthOut {
    status: &'static str,
}

/// Serves `/health` and `/ready`. A dead upstream must not take the gateway out of
/// rotation: it is the gateway that answers clients with a 503 in that case.
pub async fn health() -> Json<HealthOut> {
    Json(HealthOut { status: "ok" })
}
