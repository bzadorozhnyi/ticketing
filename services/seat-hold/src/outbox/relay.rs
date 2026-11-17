use std::future::Future;
use std::pin::pin;
use std::time::Duration;

use sqlx::Connection;
use tokio::time::{Instant, timeout_at};

use crate::core::db::DbPool;
use crate::core::rabbitmq::Publisher;
use crate::core::settings::{OutboxSettings, RabbitMqSettings};
use crate::outbox::dto::message::OutboxMessage;
use crate::outbox::repositories::message::OutboxRepository;

pub const EXCHANGE: &str = "seat-hold.events";

#[derive(Debug, thiserror::Error)]
pub enum RelayError {
    #[error("database: {0}")]
    Database(#[from] sqlx::Error),
    #[error("broker: {0}")]
    Broker(#[from] lapin::Error),
    #[error("the broker refused a message")]
    Refused,
    #[error("the broker did not confirm in time")]
    Timeout,
}

/// Publishes the outbox. A row is deleted only after the broker confirmed it, so a crash
/// can repeat a message but never lose it: delivery is at least once.
pub struct Relay<D: DbPool> {
    db: D,
    broker: RabbitMqSettings,
    settings: OutboxSettings,
    publisher: Option<Publisher>,
}

impl<D: DbPool> Relay<D> {
    #[must_use]
    pub fn new(db: D, broker: RabbitMqSettings, settings: OutboxSettings) -> Self {
        Self {
            db,
            broker,
            settings,
            publisher: None,
        }
    }

    /// Passes until `shutdown` completes. A pass in progress is finished first.
    pub async fn run(mut self, shutdown: impl Future<Output = ()>) {
        let mut shutdown = pin!(shutdown);
        loop {
            let pause = self.pass().await;
            tokio::select! {
                () = tokio::time::sleep(pause) => {}
                () = &mut shutdown => break,
            }
        }
        if let Some(publisher) = self.publisher.take() {
            publisher.close().await;
        }
    }

    /// One batch; returns how long to wait before the next one. A broker that failed is
    /// connected to again on the next pass.
    async fn pass(&mut self) -> Duration {
        let publisher = match self.publisher.take() {
            Some(publisher) => publisher,
            None => match Publisher::connect(&self.broker, self.settings.publish_timeout, EXCHANGE)
                .await
            {
                Ok(publisher) => {
                    tracing::info!("Connected to the broker");
                    publisher
                }
                Err(error) => {
                    tracing::warn!(%error, "Connecting to the broker failed");
                    return self.settings.poll_interval;
                }
            },
        };
        match self.publish_batch(&publisher).await {
            Ok(count) => {
                self.publisher = Some(publisher);
                if i64::try_from(count).is_ok_and(|count| count >= self.settings.batch_size) {
                    Duration::ZERO
                } else {
                    self.settings.poll_interval
                }
            }
            Err(error) => {
                tracing::warn!(%error, "Outbox batch failed");
                if matches!(error, RelayError::Database(_)) {
                    self.publisher = Some(publisher);
                } else {
                    publisher.close().await;
                }
                self.settings.poll_interval
            }
        }
    }

    /// Locks the oldest batch, publishes it and deletes the confirmed rows in one transaction.
    pub async fn publish_batch(&self, publisher: &Publisher) -> Result<usize, RelayError> {
        let mut conn = self.db.get_conn().await?;
        let mut transaction = conn.begin().await?;
        let messages =
            OutboxRepository::lock_batch(&mut transaction, self.settings.batch_size).await?;
        let (confirmed, failure) = self.publish(publisher, &messages).await;
        OutboxRepository::delete(&mut transaction, &confirmed).await?;
        transaction.commit().await?;
        match failure {
            Some(error) => Err(error),
            None => Ok(confirmed.len()),
        }
    }

    /// Sends the whole batch before waiting, so a batch costs one round trip to the broker,
    /// not one per message. Keeps the confirmed messages up to the first failure: a rollback
    /// of the whole batch would publish them again on every pass.
    async fn publish(
        &self,
        publisher: &Publisher,
        messages: &[OutboxMessage],
    ) -> (Vec<i64>, Option<RelayError>) {
        let mut pending = Vec::with_capacity(messages.len());
        let mut failure = None;
        for message in messages {
            match publisher
                .publish(EXCHANGE, &message.routing_key, message.body.as_bytes())
                .await
            {
                Ok(confirm) => pending.push((message.id, confirm)),
                Err(error) => {
                    failure = Some(error.into());
                    break;
                }
            }
        }
        let deadline = Instant::now() + self.settings.publish_timeout;
        let mut confirmed = Vec::with_capacity(pending.len());
        for (id, confirm) in pending {
            match timeout_at(deadline, confirm).await {
                Ok(Ok(confirmation)) if confirmation.is_ack() => confirmed.push(id),
                Ok(Ok(_)) => return (confirmed, Some(RelayError::Refused)),
                Ok(Err(error)) => return (confirmed, Some(error.into())),
                Err(_) => return (confirmed, Some(RelayError::Timeout)),
            }
        }
        (confirmed, failure)
    }
}
