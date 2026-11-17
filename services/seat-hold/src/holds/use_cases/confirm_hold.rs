use axum::extract::FromRef;
use sqlx::Connection;
use uuid::Uuid;

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::holds::dto::event::SeatEventKind;
use crate::holds::repositories::hold::HoldRepository;
use crate::holds::services::seat_events;

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

    /// Runs after a charge: a confirmed seat is sold and never expires. Safe to repeat; only
    /// the first confirm writes `SeatsSold`.
    pub async fn execute(&self, hold_id: Uuid, owner_id: &str) -> Result<(), ServiceError> {
        let mut conn = self.db.get_conn().await?;
        let mut transaction = conn.begin().await?;
        let seats = HoldRepository::confirm(&mut transaction, hold_id, owner_id).await?;
        let Some(event_id) = seats.first().map(|seat| seat.event_id) else {
            transaction.rollback().await?;
            return Err(ServiceError::HoldExpired);
        };
        let sold = seat_events::changes(&seats, false);
        seat_events::record(
            &mut transaction,
            SeatEventKind::SeatsSold,
            event_id,
            hold_id,
            sold,
        )
        .await?;
        transaction.commit().await?;
        Ok(())
    }
}
