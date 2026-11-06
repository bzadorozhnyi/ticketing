use chrono::{DateTime, Utc};
use serde::Serialize;
use utoipa::ToSchema;
use uuid::Uuid;

#[derive(Serialize, ToSchema)]
pub struct ExtendedHoldOut {
    pub hold_id: Uuid,
    pub expires_at: DateTime<Utc>,
}

#[derive(Serialize, ToSchema)]
pub struct ConfirmedHoldOut {
    pub hold_id: Uuid,
}
