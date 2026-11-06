use axum::extract::FromRef;
use uuid::Uuid;

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::holds::repositories::hold::HoldRepository;

#[derive(Clone)]
pub struct ConfirmHoldUseCase<D: DbPool> {
    db: D,
}

impl<D: DbPool> FromRef<AppState<D>> for ConfirmHoldUseCase<D> {
    fn from_ref(state: &AppState<D>) -> Self {
        Self::new(state.db.clone())
    }
}

impl<D: DbPool> ConfirmHoldUseCase<D> {
    #[must_use]
    pub fn new(db: D) -> Self {
        Self { db }
    }

    /// Runs after a charge: a confirmed seat is sold and never expires. Safe to repeat.
    pub async fn execute(&self, hold_id: Uuid, owner_id: &str) -> Result<(), ServiceError> {
        let mut conn = self.db.get_conn().await?;
        let confirmed = HoldRepository::confirm(&mut conn, hold_id, owner_id).await?;
        if confirmed == 0 {
            return Err(ServiceError::HoldExpired);
        }
        Ok(())
    }
}
