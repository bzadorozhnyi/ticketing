use std::time::Duration;

use axum::extract::FromRef;
use sqlx::Connection;
use uuid::Uuid;

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::core::time::Timestamp;
use crate::holds::dto::event::SeatEventKind;
use crate::holds::repositories::hold::HoldRepository;
use crate::holds::services::seat_events;

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
    pub async fn execute(&self, hold_id: Uuid, owner_id: &str) -> Result<Timestamp, ServiceError> {
        let mut conn = self.db.get_conn().await?;
        let mut transaction = conn.begin().await?;
        let seats = HoldRepository::extend(&mut transaction, hold_id, owner_id, self.ttl).await?;
        let (Some(event_id), Some(expires_at)) = (
            seats.first().map(|seat| seat.event_id),
            seats.iter().filter_map(|seat| seat.expires_at).min(),
        ) else {
            transaction.rollback().await?;
            return Err(ServiceError::HoldExpired);
        };
        let extended = seat_events::changes(&seats, true);
        seat_events::record(
            &mut transaction,
            SeatEventKind::HoldExtended,
            event_id,
            hold_id,
            extended,
        )
        .await?;
        transaction.commit().await?;
        Ok(expires_at)
    }
}
