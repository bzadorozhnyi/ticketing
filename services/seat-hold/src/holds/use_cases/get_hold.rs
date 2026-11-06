use axum::extract::FromRef;
use uuid::Uuid;

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::holds::dto::hold::Hold;
use crate::holds::repositories::hold::HoldRepository;

#[derive(Clone)]
pub struct GetHoldUseCase<D: DbPool> {
    db: D,
}

impl<D: DbPool> FromRef<AppState<D>> for GetHoldUseCase<D> {
    fn from_ref(state: &AppState<D>) -> Self {
        Self::new(state.db.clone())
    }
}

impl<D: DbPool> GetHoldUseCase<D> {
    #[must_use]
    pub fn new(db: D) -> Self {
        Self { db }
    }

    /// An expired hold is still returned: the saga learns that from `extend`.
    pub async fn execute(&self, hold_id: Uuid, owner_id: &str) -> Result<Hold, ServiceError> {
        let mut conn = self.db.get_conn().await?;
        HoldRepository::get(&mut conn, hold_id, owner_id)
            .await?
            .ok_or(ServiceError::NotFound)
    }
}
