use std::time::Duration;

use sqlx::PgConnection;
use uuid::Uuid;

use crate::holds::dto::hold::{Hold, NewHold};

pub struct HoldRequestRepository;

impl HoldRequestRepository {
    /// The primary key decides the race: a concurrent duplicate waits here until the first
    /// request ends. `now()` is the start of the transaction, so `expires_at` is the same
    /// value `take_seats` writes.
    pub async fn reserve(
        conn: &mut PgConnection,
        idempotency_key: &str,
        hold_id: Uuid,
        hold: &NewHold,
        ttl: Duration,
    ) -> Result<bool, sqlx::Error> {
        let result = sqlx::query!(
            r#"
            INSERT INTO hold_requests
                (owner_id, idempotency_key, hold_id, event_id, seat_ids, expires_at)
            VALUES ($1, $2, $3, $4, $5, now() + make_interval(secs => $6))
            ON CONFLICT (owner_id, idempotency_key) DO NOTHING
            "#,
            hold.owner_id,
            idempotency_key,
            hold_id,
            hold.event_id,
            &hold.seat_ids,
            ttl.as_secs_f64(),
        )
        .execute(conn)
        .await?;
        Ok(result.rows_affected() == 1)
    }

    pub async fn get(
        conn: &mut PgConnection,
        owner_id: &str,
        idempotency_key: &str,
    ) -> Result<Option<Hold>, sqlx::Error> {
        sqlx::query_as!(
            Hold,
            r#"
            SELECT hold_id, event_id, seat_ids, expires_at
            FROM hold_requests
            WHERE owner_id = $1 AND idempotency_key = $2
            "#,
            owner_id,
            idempotency_key,
        )
        .fetch_optional(conn)
        .await
    }

    pub async fn delete_old(
        conn: &mut PgConnection,
        retention: Duration,
        batch_size: i64,
    ) -> Result<u64, sqlx::Error> {
        let result = sqlx::query!(
            r#"
            DELETE FROM hold_requests
            WHERE ctid IN (
                SELECT ctid FROM hold_requests
                WHERE created_at <= now() - make_interval(secs => $1)
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            "#,
            retention.as_secs_f64(),
            batch_size,
        )
        .execute(conn)
        .await?;
        Ok(result.rows_affected())
    }
}
