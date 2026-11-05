use std::time::Duration;

use sqlx::PgConnection;
use uuid::Uuid;

use crate::holds::dto::hold::{NewHold, TakenSeat};

pub struct HoldRepository;

impl HoldRepository {
    /// Takes every seat that is free or whose hold has expired, and returns those seats.
    /// A taken seat is left out of the result, so the caller checks the count and rolls
    /// back. Seats are locked in sorted order: two requests for the same seats cannot deadlock.
    pub async fn take_seats(
        conn: &mut PgConnection,
        hold_id: Uuid,
        hold: &NewHold,
        ttl: Duration,
    ) -> Result<Vec<TakenSeat>, sqlx::Error> {
        sqlx::query_as!(
            TakenSeat,
            r#"
            INSERT INTO holds (event_id, seat_id, hold_id, owner_id, expires_at)
            SELECT $1, requested.seat_id, $2, $3, now() + make_interval(secs => $4)
            FROM unnest($5::uuid[]) AS requested (seat_id)
            ORDER BY requested.seat_id
            ON CONFLICT (event_id, seat_id) DO UPDATE
            SET hold_id = EXCLUDED.hold_id,
                owner_id = EXCLUDED.owner_id,
                expires_at = EXCLUDED.expires_at
            WHERE holds.expires_at <= now()
            RETURNING seat_id, expires_at
            "#,
            hold.event_id,
            hold_id,
            hold.owner_id,
            ttl.as_secs_f64(),
            &hold.seat_ids,
        )
        .fetch_all(conn)
        .await
    }

    pub async fn release(
        conn: &mut PgConnection,
        hold_id: Uuid,
        owner_id: &str,
    ) -> Result<u64, sqlx::Error> {
        let result = sqlx::query!(
            "DELETE FROM holds WHERE hold_id = $1 AND owner_id = $2",
            hold_id,
            owner_id,
        )
        .execute(conn)
        .await?;
        Ok(result.rows_affected())
    }

    /// `SKIP LOCKED` leaves a row that someone is taking over right now, and lets
    /// several replicas clean up at once without waiting for each other.
    pub async fn delete_expired(
        conn: &mut PgConnection,
        batch_size: i64,
    ) -> Result<u64, sqlx::Error> {
        let result = sqlx::query!(
            r#"
            DELETE FROM holds
            WHERE ctid IN (
                SELECT ctid FROM holds
                WHERE expires_at <= now()
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            "#,
            batch_size,
        )
        .execute(conn)
        .await?;
        Ok(result.rows_affected())
    }
}
