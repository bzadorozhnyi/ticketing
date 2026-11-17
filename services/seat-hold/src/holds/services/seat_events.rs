use sqlx::PgConnection;
use uuid::Uuid;

use crate::core::errors::ServiceError;
use crate::holds::dto::event::{SeatChange, SeatEvent, SeatEventKind};
use crate::holds::dto::hold::ChangedSeat;
use crate::outbox::repositories::message::OutboxRepository;

/// Writes an event of the seats that changed into the outbox, in the caller's transaction:
/// the event exists exactly when the change does. Nothing changed, nothing is written.
pub async fn record(
    conn: &mut PgConnection,
    kind: SeatEventKind,
    event_id: Uuid,
    hold_id: Uuid,
    seats: Vec<SeatChange>,
) -> Result<(), ServiceError> {
    if seats.is_empty() {
        return Ok(());
    }
    let event = SeatEvent {
        kind,
        event_id,
        hold_id,
        seats,
    };
    let body = serde_json::to_string(&event).map_err(|_| ServiceError::Internal)?;
    OutboxRepository::insert(conn, kind.routing_key(), &body).await?;
    Ok(())
}

/// The seats a statement really changed, as the event reports them; `with_expiry` keeps the
/// time a held seat is taken until.
#[must_use]
pub fn changes(seats: &[ChangedSeat], with_expiry: bool) -> Vec<SeatChange> {
    seats
        .iter()
        .filter(|seat| seat.changed)
        .filter_map(|seat| {
            Some(SeatChange {
                seat_id: seat.seat_id,
                version: seat.version?,
                expires_at: if with_expiry { seat.expires_at } else { None },
            })
        })
        .collect()
}
