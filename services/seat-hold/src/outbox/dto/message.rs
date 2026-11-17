use chrono::{DateTime, Utc};

pub struct OutboxMessage {
    pub id: i64,
    pub routing_key: String,
    pub body: String,
    pub created_at: DateTime<Utc>,
}
