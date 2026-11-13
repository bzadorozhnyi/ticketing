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
use crate::holds::repositories::hold_request::HoldRequestRepository;

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

    /// All seats or none: if any seat is taken, the transaction is rolled back, and the
    /// key with it, so a refused request can be tried again. With a key, a repeat of a
    /// successful request returns the first result, even after the hold has ended and
    /// another user took the seats: the stored result is looked up before the seats.
    ///
    /// A seat held by another user is refused after a read, before any lock: on a hot
    /// seat every refusal would otherwise queue on its row lock and hold a pooled
    /// connection while it waits. The read only refuses; the insert below still decides.
    /// Two requests with one key at once both pass the lookup; the second then waits on
    /// the key's row in `reserve` and replays.
    ///
    /// A user holds at most `max_seats_per_user` seats of one event at once. The requests
    /// of one user for one event take turns on a lock, so two of them cannot both count
    /// the same free places.
    pub async fn execute(
        &self,
        hold: NewHold,
        idempotency_key: Option<&str>,
    ) -> Result<Hold, ServiceError> {
        self.validate(&hold.seat_ids)?;

        let hold_id = Uuid::now_v7();
        let ttl = self.settings.ttl;
        let mut conn = self.db.get_conn().await?;
        if let Some(key) = idempotency_key
            && let Some(stored) = HoldRequestRepository::get(&mut conn, &hold.owner_id, key).await?
        {
            return Self::replay(stored, &hold);
        }
        let holders = HoldRepository::holders(&mut conn, hold.event_id, &hold.seat_ids).await?;
        if holders.iter().any(|seat| seat.owner_id != hold.owner_id) {
            let taken = hold
                .seat_ids
                .into_iter()
                .filter(|seat_id| holders.iter().any(|seat| seat.seat_id == *seat_id))
                .collect();
            return Err(ServiceError::SeatsTaken(taken));
        }
        let mut transaction = conn.begin().await?;

        if let Some(key) = idempotency_key {
            let reserved =
                HoldRequestRepository::reserve(&mut transaction, key, hold_id, &hold, ttl).await?;
            if !reserved {
                let stored = HoldRequestRepository::get(&mut transaction, &hold.owner_id, key)
                    .await?
                    .ok_or(ServiceError::Internal)?;
                transaction.rollback().await?;
                return Self::replay(stored, &hold);
            }
        }

        HoldRepository::lock_owner(&mut transaction, hold.event_id, &hold.owner_id).await?;
        let seats_held =
            HoldRepository::count_held(&mut transaction, hold.event_id, &hold.owner_id).await?;
        let limit = self.settings.max_seats_per_user;
        if seats_held + i64::try_from(hold.seat_ids.len()).unwrap_or(i64::MAX) > limit {
            transaction.rollback().await?;
            return Err(ServiceError::HoldLimitReached(limit));
        }

        let taken = HoldRepository::take_seats(&mut transaction, hold_id, &hold, ttl).await?;
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

    fn replay(stored: Hold, hold: &NewHold) -> Result<Hold, ServiceError> {
        let same_seats = stored.seat_ids.iter().collect::<HashSet<_>>()
            == hold.seat_ids.iter().collect::<HashSet<_>>();
        if stored.event_id != hold.event_id || !same_seats {
            return Err(ServiceError::Validation(
                "Idempotency-Key was used with another request".to_owned(),
            ));
        }
        Ok(stored)
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
