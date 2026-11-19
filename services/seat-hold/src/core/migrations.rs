//! Migrations that cannot stall the service, see ADR 0008.
//!
//! A migration waiting for a lock holds a place in the lock queue, and every later query
//! on that table waits behind it, reads included. So the migration's session gives up a
//! lock after `MIGRATIONS__LOCK_TIMEOUT_SECONDS` and the run is tried again after a pause.
//! A migration in a transaction leaves nothing behind when it fails. One marked
//! `-- no-transaction`, such as `CREATE INDEX CONCURRENTLY`, leaves an invalid index,
//! which the next attempt drops before it runs the migrations again.

use std::future::Future;
use std::time::Duration;

use sqlx::error::DatabaseError;
use sqlx::migrate::{Migrate, MigrateError, Migrator};
use sqlx::{PgConnection, PgPool};

use crate::core::settings::MigrationSettings;

const LOCK_NOT_AVAILABLE: &str = "55P03";

/// Runs the pending migrations, tried again while a lock is not available.
///
/// # Errors
/// As [`run_with_lock_retries`].
pub async fn run_migrations(
    migrator: &Migrator,
    pool: &PgPool,
    settings: &MigrationSettings,
) -> Result<(), MigrateError> {
    run_with_lock_retries(settings, || attempt(migrator, pool)).await
}

/// One attempt on a connection of its own, under the migrator's advisory lock: another
/// `migrate` cannot drop an index this one is still building. The connection is closed at
/// the end, which releases the lock even when a step failed half way.
async fn attempt(migrator: &Migrator, pool: &PgPool) -> Result<(), MigrateError> {
    let mut conn = pool.acquire().await?.detach();
    conn.lock().await?;
    drop_invalid_indexes(&mut conn).await?;
    migrator.run(&mut conn).await
}

/// Postgres keeps an index whose `CREATE INDEX CONCURRENTLY` failed: never read, still
/// written, and its name blocks the next try.
async fn drop_invalid_indexes(conn: &mut PgConnection) -> Result<(), MigrateError> {
    let indexes = sqlx::query_scalar!(
        r#"
        SELECT format('%I.%I', n.nspname, c.relname) AS "name!"
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE NOT i.indisvalid AND n.nspname = current_schema()
        "#
    )
    .fetch_all(&mut *conn)
    .await?;
    for index in indexes {
        tracing::warn!(%index, "Dropping an index left invalid by a failed migration");
        sqlx::query(sqlx::AssertSqlSafe(format!(
            "DROP INDEX CONCURRENTLY IF EXISTS {index}"
        )))
        .execute(&mut *conn)
        .await?;
    }
    Ok(())
}

/// # Errors
/// The last error once the attempts run out, or the first error that is not a lock timeout.
pub async fn run_with_lock_retries<F, Fut>(
    settings: &MigrationSettings,
    mut attempt: F,
) -> Result<(), MigrateError>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<(), MigrateError>>,
{
    let mut number = 1;
    loop {
        match attempt().await {
            Err(error) if number < settings.attempts && is_lock_timeout(&error) => {
                let delay = retry_delay(settings, number);
                tracing::warn!(
                    attempt = number,
                    ?delay,
                    "Migration could not take a lock, will try again"
                );
                tokio::time::sleep(delay).await;
                number += 1;
            }
            result => return result,
        }
    }
}

#[must_use]
pub fn is_lock_timeout(error: &MigrateError) -> bool {
    let (MigrateError::Execute(source) | MigrateError::ExecuteMigration(source, _)) = error else {
        return false;
    };
    source
        .as_database_error()
        .and_then(DatabaseError::code)
        .is_some_and(|code| code == LOCK_NOT_AVAILABLE)
}

fn retry_delay(settings: &MigrationSettings, attempt: u32) -> Duration {
    let doubled = settings
        .retry_base_delay
        .saturating_mul(2_u32.saturating_pow(attempt.saturating_sub(1)));
    doubled.min(settings.max_retry_delay)
}
