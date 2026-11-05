use chrono::{DateTime, Utc};
use uuid::Uuid;

pub struct NewHold {
    pub event_id: Uuid,
    pub seat_ids: Vec<Uuid>,
    pub owner_id: String,
}

pub struct Hold {
    pub hold_id: Uuid,
    pub event_id: Uuid,
    pub seat_ids: Vec<Uuid>,
    pub expires_at: DateTime<Utc>,
}

pub struct TakenSeat {
    pub seat_id: Uuid,
    pub expires_at: DateTime<Utc>,
}
