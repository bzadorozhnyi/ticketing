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
