use serde::Serialize;
use utoipa::ToSchema;
use uuid::Uuid;

use crate::core::time::{Timestamp, rfc3339};

#[derive(Serialize, ToSchema)]
pub struct ExtendedHoldOut {
    pub hold_id: Uuid,
    #[serde(with = "rfc3339")]
    #[schema(value_type = String, format = DateTime)]
    pub expires_at: Timestamp,
}

#[derive(Serialize, ToSchema)]
pub struct ConfirmedHoldOut {
    pub hold_id: Uuid,
}
