use crate::core::time::Timestamp;

pub struct OutboxMessage {
    pub id: i64,
    pub routing_key: String,
    pub body: String,
    pub created_at: Timestamp,
}
