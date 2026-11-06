use std::time::Duration;

use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::holds::repositories::hold::HoldRepository;
use crate::holds::repositories::hold_request::HoldRequestRepository;

#[derive(Clone)]
pub struct CleanupExpiredHoldsUseCase<D: DbPool> {
    db: D,
    batch_size: i64,
    retention: Duration,
}

impl<D: DbPool> CleanupExpiredHoldsUseCase<D> {
    #[must_use]
    pub fn new(db: D, batch_size: i64, retention: Duration) -> Self {
        Self {
            db,
            batch_size,
            retention,
        }
    }

    /// Deletes in batches, so one run never holds many row locks at once.
    pub async fn execute(&self) -> Result<u64, ServiceError> {
        let mut total = 0;
        loop {
            let mut conn = self.db.get_conn().await?;
            let holds = HoldRepository::delete_expired(&mut conn, self.batch_size).await?;
            let keys =
                HoldRequestRepository::delete_old(&mut conn, self.retention, self.batch_size)
                    .await?;
            total += holds + keys;
            if holds.max(keys) < self.batch_size.unsigned_abs() {
                return Ok(total);
            }
        }
    }
}
