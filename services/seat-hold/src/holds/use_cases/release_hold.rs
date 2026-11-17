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
pub struct ReleaseHoldUseCase<D: DbPool> {
    db: D,
}

impl<D: DbPool> FromRef<AppState<D>> for ReleaseHoldUseCase<D> {
    fn from_ref(state: &AppState<D>) -> Self {
        Self::new(state.db.clone())
    }
}

impl<D: DbPool> ReleaseHoldUseCase<D> {
    #[must_use]
    pub fn new(db: D) -> Self {
        Self { db }
    }

    /// Safe to repeat: the goal is that the caller holds nothing under this id, so a hold
    /// that is already gone counts as success. Another user's hold stays untouched.
    pub async fn execute(&self, hold_id: Uuid, owner_id: &str) -> Result<(), ServiceError> {
        let mut conn = self.db.get_conn().await?;
        let mut transaction = conn.begin().await?;
        let seats = HoldRepository::release(&mut transaction, hold_id, owner_id).await?;
        if let Some(event_id) = seats.first().map(|seat| seat.event_id) {
            let released = seat_events::changes(&seats, false);
            seat_events::record(
                &mut transaction,
                SeatEventKind::SeatsReleased,
                event_id,
                hold_id,
                released,
            )
            .await?;
        }
        transaction.commit().await?;
        Ok(())
    }
}
