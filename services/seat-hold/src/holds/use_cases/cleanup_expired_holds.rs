use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::holds::repositories::hold::HoldRepository;

#[derive(Clone)]
pub struct CleanupExpiredHoldsUseCase<D: DbPool> {
    db: D,
    batch_size: i64,
}

impl<D: DbPool> CleanupExpiredHoldsUseCase<D> {
    #[must_use]
    pub fn new(db: D, batch_size: i64) -> Self {
        Self { db, batch_size }
    }

    /// Deletes in batches, so one run never holds many row locks at once.
    pub async fn execute(&self) -> Result<u64, ServiceError> {
        let mut total = 0;
        loop {
            let mut conn = self.db.get_conn().await?;
            let deleted = HoldRepository::delete_expired(&mut conn, self.batch_size).await?;
            total += deleted;
            if deleted < self.batch_size.unsigned_abs() {
                return Ok(total);
            }
        }
    }
}
