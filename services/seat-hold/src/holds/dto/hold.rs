use uuid::Uuid;

use crate::core::time::Timestamp;

pub struct NewHold {
    pub event_id: Uuid,
    pub seat_ids: Vec<Uuid>,
    pub owner_id: String,
}

pub struct Hold {
    pub hold_id: Uuid,
    pub event_id: Uuid,
    pub seat_ids: Vec<Uuid>,
    pub expires_at: Timestamp,
}

pub struct SeatHolder {
    pub seat_id: Uuid,
    pub owner_id: String,
}

pub struct TakenSeat {
    pub seat_id: Uuid,
    pub expires_at: Timestamp,
}

/// A seat whose row a statement changed, with the version it took. `changed` is false when
/// the statement left the seat as it was, as a repeated confirm or extend does.
pub struct ChangedSeat {
    pub event_id: Uuid,
    pub seat_id: Uuid,
    pub version: Option<i64>,
    pub expires_at: Option<Timestamp>,
    pub changed: bool,
}
