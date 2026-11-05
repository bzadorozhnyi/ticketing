use std::time::Duration;

use tokio::time::{self, MissedTickBehavior};

use crate::core::db::DbPool;
use crate::holds::use_cases::cleanup_expired_holds::CleanupExpiredHoldsUseCase;

/// Removes expired rows so the table does not grow. Correctness does not depend on it:
/// an expired hold already counts as free when a seat is taken.
pub async fn run<D: DbPool>(use_case: CleanupExpiredHoldsUseCase<D>, period: Duration) {
    let mut ticker = time::interval(period);
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
    loop {
        ticker.tick().await;
        match use_case.execute().await {
            Ok(0) => {}
            Ok(deleted) => tracing::info!(deleted, "Expired holds removed"),
            Err(error) => tracing::warn!(%error, "Expired holds cleanup failed"),
        }
    }
}
