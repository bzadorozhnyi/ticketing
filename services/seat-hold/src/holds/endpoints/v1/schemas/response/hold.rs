use chrono::{DateTime, Utc};
use serde::Serialize;
use utoipa::ToSchema;
use uuid::Uuid;

use crate::holds::dto::hold::Hold;

#[derive(Serialize, ToSchema)]
pub struct HoldOut {
    pub hold_id: Uuid,
    pub event_id: Uuid,
    pub seat_ids: Vec<Uuid>,
    pub expires_at: DateTime<Utc>,
}

impl From<Hold> for HoldOut {
    fn from(hold: Hold) -> Self {
        Self {
            hold_id: hold.hold_id,
            event_id: hold.event_id,
            seat_ids: hold.seat_ids,
            expires_at: hold.expires_at,
        }
    }
}
