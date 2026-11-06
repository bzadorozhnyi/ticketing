use bon::builder;
use seat_hold::core::db::DbPool;
use uuid::Uuid;

use super::db::TestPool;

#[allow(clippy::struct_field_names)]
pub struct HoldRow {
    pub event_id: Uuid,
    pub seat_id: Uuid,
    pub hold_id: Uuid,
}

#[builder]
pub async fn hold_factory(
    pool: &TestPool,
    event_id: Option<Uuid>,
    seat_id: Option<Uuid>,
    hold_id: Option<Uuid>,
    owner_id: Option<String>,
    expires_in_seconds: Option<f64>,
    status: Option<&str>,
) -> HoldRow {
    let event_id = event_id.unwrap_or_else(Uuid::now_v7);
    let seat_id = seat_id.unwrap_or_else(Uuid::now_v7);
    let hold_id = hold_id.unwrap_or_else(Uuid::now_v7);
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query(
        "INSERT INTO holds (event_id, seat_id, hold_id, owner_id, expires_at, status)
         VALUES ($1, $2, $3, $4, now() + make_interval(secs => $5), $6)",
    )
    .bind(event_id)
    .bind(seat_id)
    .bind(hold_id)
    .bind(owner_id.unwrap_or_else(|| "someone-else".to_owned()))
    .bind(expires_in_seconds.unwrap_or(300.0))
    .bind(status.unwrap_or("held"))
    .execute(&mut *conn)
    .await
    .expect("a stored hold");
    HoldRow {
        event_id,
        seat_id,
        hold_id,
    }
}
