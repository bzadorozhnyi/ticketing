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
}
