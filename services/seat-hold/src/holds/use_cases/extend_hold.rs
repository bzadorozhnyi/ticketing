use std::time::Duration;

use axum::extract::FromRef;
use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::holds::repositories::hold::HoldRepository;

#[derive(Clone)]
pub struct ExtendHoldUseCase<D: DbPool> {
    db: D,
    ttl: Duration,
}

impl<D: DbPool> FromRef<AppState<D>> for ExtendHoldUseCase<D> {
    fn from_ref(state: &AppState<D>) -> Self {
        Self::new(state.db.clone(), state.holds.payment_ttl)
    }
}

impl<D: DbPool> ExtendHoldUseCase<D> {
    #[must_use]
    pub fn new(db: D, ttl: Duration) -> Self {
        Self { db, ttl }
    }

    /// Runs before a charge: if the hold is gone, no money is taken. Safe to repeat.
    ///
    /// Only the first call adds time. A declined card keeps the hold so the user can try
    /// another, but new orders cannot keep a seat for ever: a hold lasts at most `ttl`
    /// plus `payment_ttl`. A later order gets the time that is left.
    pub async fn execute(
        &self,
        hold_id: Uuid,
        owner_id: &str,
    ) -> Result<DateTime<Utc>, ServiceError> {
        let mut conn = self.db.get_conn().await?;
        let expires_at = HoldRepository::extend(&mut conn, hold_id, owner_id, self.ttl).await?;
        expires_at
            .into_iter()
            .min()
            .ok_or(ServiceError::HoldExpired)
    }
}
