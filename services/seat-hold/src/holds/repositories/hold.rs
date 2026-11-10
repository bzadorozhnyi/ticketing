use std::time::Duration;

use chrono::{DateTime, Utc};
use sqlx::PgConnection;
use uuid::Uuid;

use crate::core::telemetry::db::db_span;
use crate::holds::dto::hold::{Hold, NewHold, SeatHolder, TakenSeat};

pub struct HoldRepository;

impl HoldRepository {
    /// Holds the requests of one user for one event back until the transaction ends, so
    /// a count of their seats stays true until the insert. Other users never wait.
    pub async fn lock_owner(
        conn: &mut PgConnection,
        event_id: Uuid,
        owner_id: &str,
    ) -> Result<(), sqlx::Error> {
        let _span = db_span("SELECT", "pg_advisory_xact_lock");
        sqlx::query!(
            r#"
            SELECT 1 AS "locked!"
            FROM pg_advisory_xact_lock(hashtextextended($1 || ':' || $2::uuid::text, 0))
            "#,
            owner_id,
            event_id,
        )
        .fetch_one(conn)
        .await?;
        Ok(())
    }

    /// Seats the user holds for the event now; sold ones are not counted.
    pub async fn count_held(
        conn: &mut PgConnection,
        event_id: Uuid,
        owner_id: &str,
    ) -> Result<i64, sqlx::Error> {
        let _span = db_span("SELECT", "holds");
        sqlx::query_scalar!(
            r#"
            SELECT count(*) AS "seats!"
            FROM holds
            WHERE event_id = $1 AND owner_id = $2 AND status = 'held' AND expires_at > now()
            "#,
            event_id,
            owner_id,
        )
        .fetch_one(conn)
        .await
    }

    /// The seats among `seat_ids` that are held or sold, with their owners. A plain read
    /// without a lock, so it may be stale by the time the caller acts on it.
    pub async fn holders(
        conn: &mut PgConnection,
        event_id: Uuid,
        seat_ids: &[Uuid],
    ) -> Result<Vec<SeatHolder>, sqlx::Error> {
        let _span = db_span("SELECT", "holds");
        sqlx::query_as!(
            SeatHolder,
            r#"
            SELECT seat_id, owner_id
            FROM holds
            WHERE event_id = $1
              AND seat_id = ANY($2)
              AND (status = 'confirmed' OR expires_at > now())
            "#,
            event_id,
            seat_ids,
        )
        .fetch_all(conn)
        .await
    }

    /// Takes every seat that is free or whose hold has expired, and returns those seats.
    /// A taken seat is left out of the result, so the caller checks the count and rolls
    /// back. Seats are locked in sorted order: two requests for the same seats cannot deadlock.
    pub async fn take_seats(
        conn: &mut PgConnection,
        hold_id: Uuid,
        hold: &NewHold,
        ttl: Duration,
    ) -> Result<Vec<TakenSeat>, sqlx::Error> {
        let _span = db_span("INSERT", "holds");
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
                expires_at = EXCLUDED.expires_at,
                extended_at = NULL
            WHERE holds.expires_at <= now() AND holds.status = 'held'
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

    pub async fn get(
        conn: &mut PgConnection,
        hold_id: Uuid,
        owner_id: &str,
    ) -> Result<Option<Hold>, sqlx::Error> {
        let _span = db_span("SELECT", "holds");
        sqlx::query_as!(
            Hold,
            r#"
            SELECT
                $1::uuid AS "hold_id!",
                event_id,
                array_agg(seat_id ORDER BY seat_id) AS "seat_ids!",
                min(expires_at) AS "expires_at!"
            FROM holds
            WHERE hold_id = $1 AND owner_id = $2
            GROUP BY event_id
            "#,
            hold_id,
            owner_id,
        )
        .fetch_optional(conn)
        .await
    }

    /// A hold is extended once; later calls return its time unchanged. A confirmed hold
    /// counts as extended, so a repeat after a crash still succeeds.
    pub async fn extend(
        conn: &mut PgConnection,
        hold_id: Uuid,
        owner_id: &str,
        ttl: Duration,
    ) -> Result<Vec<DateTime<Utc>>, sqlx::Error> {
        let _span = db_span("UPDATE", "holds");
        sqlx::query_scalar!(
            r#"
            UPDATE holds
            SET expires_at = CASE
                    WHEN extended_at IS NULL
                    THEN GREATEST(expires_at, now() + make_interval(secs => $3))
                    ELSE expires_at
                END,
                extended_at = COALESCE(extended_at, now())
            WHERE hold_id = $1
              AND owner_id = $2
              AND (status = 'confirmed' OR expires_at > now())
            RETURNING expires_at
            "#,
            hold_id,
            owner_id,
            ttl.as_secs_f64(),
        )
        .fetch_all(conn)
        .await
    }

    pub async fn confirm(
        conn: &mut PgConnection,
        hold_id: Uuid,
        owner_id: &str,
    ) -> Result<u64, sqlx::Error> {
        let _span = db_span("UPDATE", "holds");
        let result = sqlx::query!(
            r#"
            UPDATE holds
            SET status = 'confirmed'
            WHERE hold_id = $1
              AND owner_id = $2
              AND (status = 'confirmed' OR expires_at > now())
            "#,
            hold_id,
            owner_id,
        )
        .execute(conn)
        .await?;
        Ok(result.rows_affected())
    }

    pub async fn release(
        conn: &mut PgConnection,
        hold_id: Uuid,
        owner_id: &str,
    ) -> Result<u64, sqlx::Error> {
        let _span = db_span("DELETE", "holds");
        let result = sqlx::query!(
            "DELETE FROM holds WHERE hold_id = $1 AND owner_id = $2 AND status = 'held'",
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
        let _span = db_span("DELETE", "holds");
        let result = sqlx::query!(
            r#"
            DELETE FROM holds
            WHERE ctid IN (
                SELECT ctid FROM holds
                WHERE expires_at <= now() AND status = 'held'
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
