//! Migrations that cannot stall the service, see ADR 0008.
//!
//! A migration waiting for a lock holds a place in the lock queue, and every later query
//! on that table waits behind it, reads included. So the migration's session gives up a
//! lock after `MIGRATIONS__LOCK_TIMEOUT_SECONDS` and the run is tried again after a pause.
//! Each migration is one transaction, so a failed attempt leaves nothing behind.

use std::future::Future;
use std::time::Duration;

use sqlx::error::DatabaseError;
use sqlx::migrate::MigrateError;

use crate::core::settings::MigrationSettings;

const LOCK_NOT_AVAILABLE: &str = "55P03";

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
