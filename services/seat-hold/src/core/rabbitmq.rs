use std::time::Duration;

use lapin::options::{BasicPublishOptions, ConfirmSelectOptions, ExchangeDeclareOptions};
use lapin::types::FieldTable;
use lapin::uri::{AMQPAuthority, AMQPQueryString, AMQPUri, AMQPUserInfo};
use lapin::{
    BasicProperties, Channel, Connect, Connection, ConnectionProperties, Error, ExchangeKind,
    PublisherConfirm,
};

use crate::core::settings::RabbitMqSettings;

/// AMQP delivery mode 2: a queue that is durable keeps the message over a broker restart.
const PERSISTENT: u8 = 2;

/// One connection with one channel in confirm mode: every publish is answered by the broker.
pub struct Publisher {
    connection: Connection,
    channel: Channel,
}

impl Publisher {
    /// Connects and declares `exchange` as a durable topic exchange, so publishing never
    /// depends on who created it first.
    pub async fn connect(
        settings: &RabbitMqSettings,
        timeout: Duration,
        exchange: &str,
    ) -> Result<Self, Error> {
        let uri = AMQPUri {
            authority: AMQPAuthority {
                userinfo: AMQPUserInfo {
                    username: settings.user.clone(),
                    password: settings.pass.clone(),
                },
                host: settings.host.clone(),
                port: settings.port,
            },
            query: AMQPQueryString {
                connection_timeout: u64::try_from(timeout.as_millis()).ok(),
                ..AMQPQueryString::default()
            },
            ..AMQPUri::default()
        };
        let properties =
            ConnectionProperties::default().with_connection_name("seat-hold-relay".into());
        let connection = uri.connect(properties).await?;
        let channel = connection.create_channel().await?;
        channel
            .confirm_select(ConfirmSelectOptions::default())
            .await?;
        channel
            .exchange_declare(
                exchange.into(),
                ExchangeKind::Topic,
                ExchangeDeclareOptions {
                    durable: true,
                    ..ExchangeDeclareOptions::default()
                },
                FieldTable::default(),
            )
            .await?;
        Ok(Self {
            connection,
            channel,
        })
    }

    /// Sends the message; the returned future resolves when the broker confirms it.
    pub async fn publish(
        &self,
        exchange: &str,
        routing_key: &str,
        body: &[u8],
    ) -> Result<PublisherConfirm, Error> {
        self.channel
            .basic_publish(
                exchange.into(),
                routing_key.into(),
                BasicPublishOptions::default(),
                body,
                BasicProperties::default()
                    .with_content_type("application/json".into())
                    .with_delivery_mode(PERSISTENT),
            )
            .await
    }

    pub async fn close(&self) {
        if let Err(error) = self.connection.close(200, "relay stopped".into()).await {
            tracing::debug!(%error, "Closing the broker connection failed");
        }
    }
}
