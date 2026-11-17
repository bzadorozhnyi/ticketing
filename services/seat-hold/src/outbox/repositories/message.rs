use sqlx::PgConnection;

use crate::outbox::dto::message::OutboxMessage;

use crate::core::telemetry::db::db_span;

pub struct OutboxRepository;

impl OutboxRepository {
    pub async fn insert(
        conn: &mut PgConnection,
        routing_key: &str,
        body: &str,
    ) -> Result<(), sqlx::Error> {
        let _span = db_span("INSERT", "outbox");
        sqlx::query!(
            "INSERT INTO outbox (routing_key, body) VALUES ($1, $2::text::jsonb)",
            routing_key,
            body,
        )
        .execute(conn)
        .await?;
        Ok(())
    }

    /// Oldest first. A row locked by another relay is skipped, not waited for.
    pub async fn lock_batch(
        conn: &mut PgConnection,
        limit: i64,
    ) -> Result<Vec<OutboxMessage>, sqlx::Error> {
        let _span = db_span("SELECT", "outbox");
        sqlx::query_as!(
            OutboxMessage,
            r#"SELECT id, routing_key, body::text AS "body!", created_at
            FROM outbox ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED"#,
            limit,
        )
        .fetch_all(conn)
        .await
    }

    pub async fn delete(conn: &mut PgConnection, ids: &[i64]) -> Result<(), sqlx::Error> {
        let _span = db_span("DELETE", "outbox");
        sqlx::query!("DELETE FROM outbox WHERE id = ANY($1)", ids)
            .execute(conn)
            .await?;
        Ok(())
    }
}
