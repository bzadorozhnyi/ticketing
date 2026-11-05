use serde::Deserialize;
use utoipa::ToSchema;
use uuid::Uuid;

#[derive(Deserialize, ToSchema)]
pub struct HoldIn {
    pub event_id: Uuid,
    pub seat_ids: Vec<Uuid>,
}
