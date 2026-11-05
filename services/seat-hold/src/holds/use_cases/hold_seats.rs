use std::collections::HashSet;

use axum::extract::FromRef;
use sqlx::Connection;
use uuid::Uuid;

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::core::errors::ServiceError;
use crate::core::settings::HoldSettings;
use crate::holds::dto::hold::{Hold, NewHold};
use crate::holds::repositories::hold::HoldRepository;

#[derive(Clone)]
pub struct HoldSeatsUseCase<D: DbPool> {
    db: D,
    settings: HoldSettings,
}

impl<D: DbPool> FromRef<AppState<D>> for HoldSeatsUseCase<D> {
    fn from_ref(state: &AppState<D>) -> Self {
        Self::new(state.db.clone(), state.holds)
    }
}

impl<D: DbPool> HoldSeatsUseCase<D> {
    #[must_use]
    pub fn new(db: D, settings: HoldSettings) -> Self {
        Self { db, settings }
    }

    /// All seats or none: if any seat is taken, the transaction is rolled back.
    pub async fn execute(&self, hold: NewHold) -> Result<Hold, ServiceError> {
        self.validate(&hold.seat_ids)?;

        let hold_id = Uuid::now_v7();
        let mut conn = self.db.get_conn().await?;
        let mut transaction = conn.begin().await?;

        let taken =
            HoldRepository::take_seats(&mut transaction, hold_id, &hold, self.settings.ttl).await?;
        if taken.len() != hold.seat_ids.len() {
            transaction.rollback().await?;
            let unavailable = hold
                .seat_ids
                .into_iter()
                .filter(|seat_id| !taken.iter().any(|seat| seat.seat_id == *seat_id))
                .collect();
            return Err(ServiceError::SeatsTaken(unavailable));
        }
        transaction.commit().await?;

        let expires_at = taken
            .iter()
            .map(|seat| seat.expires_at)
            .min()
            .ok_or(ServiceError::Internal)?;
        Ok(Hold {
            hold_id,
            event_id: hold.event_id,
            seat_ids: hold.seat_ids,
            expires_at,
        })
    }

    fn validate(&self, seat_ids: &[Uuid]) -> Result<(), ServiceError> {
        let max_seats = self.settings.max_seats;
        if seat_ids.is_empty() {
            return Err(ServiceError::Validation("seat_ids is empty".to_owned()));
        }
        if seat_ids.len() > max_seats {
            return Err(ServiceError::Validation(format!(
                "seat_ids has more than {max_seats} seats"
            )));
        }
        let unique: HashSet<&Uuid> = seat_ids.iter().collect();
        if unique.len() != seat_ids.len() {
            return Err(ServiceError::Validation(
                "seat_ids has duplicates".to_owned(),
            ));
        }
        Ok(())
    }
}
