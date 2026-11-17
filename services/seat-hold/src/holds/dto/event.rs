use serde::Serialize;
use uuid::Uuid;

use crate::core::time::{Timestamp, rfc3339};

/// What happened to some seats of one hold. Published to other services, so its shape is a contract.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum SeatEventKind {
    SeatsHeld,
    HoldExtended,
    SeatsSold,
    SeatsReleased,
}

impl SeatEventKind {
    #[must_use]
    pub fn routing_key(self) -> &'static str {
        match self {
            Self::SeatsHeld => "seats.held",
            Self::HoldExtended => "seats.extended",
            Self::SeatsSold => "seats.sold",
            Self::SeatsReleased => "seats.released",
        }
    }
}

/// One seat's new state. `version` grows with every change of the seat; a reader keeps the
/// latest. A held seat is taken until `expires_at`; there is no event when that passes.
#[derive(Clone, Debug, Serialize)]
pub struct SeatChange {
    pub seat_id: Uuid,
    pub version: i64,
    #[serde(skip_serializing_if = "Option::is_none", with = "rfc3339::option")]
    pub expires_at: Option<Timestamp>,
}

#[derive(Debug, Serialize)]
pub struct SeatEvent {
    #[serde(rename = "type")]
    pub kind: SeatEventKind,
    pub event_id: Uuid,
    pub hold_id: Uuid,
    pub seats: Vec<SeatChange>,
}
