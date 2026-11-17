mod common;

use std::time::Duration;

use common::db::TestPool;
use lapin::options::{BasicGetOptions, QueueBindOptions, QueueDeclareOptions};
use lapin::types::FieldTable;
use lapin::{Channel, Connection, ConnectionProperties};
use seat_hold::core::db::DbPool;
use seat_hold::core::rabbitmq::Publisher;
use seat_hold::core::settings::{OutboxSettings, RabbitMqSettings};
use seat_hold::outbox::relay::{EXCHANGE, Relay};
use seat_hold::outbox::repositories::message::OutboxRepository;
use serde_json::{Value, json};
use uuid::Uuid;

const BROKER: &str = "amqp://ticketing:password@localhost:5672/%2f";

fn broker() -> RabbitMqSettings {
    RabbitMqSettings {
        host: "localhost".to_owned(),
        port: 5672,
        user: "ticketing".to_owned(),
        pass: "password".to_owned(),
    }
}

fn settings(batch_size: i64) -> OutboxSettings {
    OutboxSettings {
        batch_size,
        ..OutboxSettings::default()
    }
}

/// Every test publishes with its own marker, since tests running at once share the exchange.
async fn write(pool: &TestPool, marker: Uuid, routing_keys: &[&str]) {
    let mut conn = pool.get_conn().await.expect("a test connection");
    for (n, routing_key) in routing_keys.iter().enumerate() {
        let body = json!({"marker": marker, "n": n}).to_string();
        OutboxRepository::insert(&mut conn, routing_key, &body)
            .await
            .expect("an outbox row");
    }
}

async fn outbox_size(pool: &TestPool) -> i64 {
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query_scalar("SELECT count(*) FROM outbox")
        .fetch_one(&mut *conn)
        .await
        .expect("the outbox size")
}

/// A queue of the test's own, bound to every event, removed with the connection.
async fn subscribe() -> (Connection, Channel, String) {
    let connection = Connection::connect(BROKER, ConnectionProperties::default())
        .await
        .expect("a broker connection");
    let channel = connection.create_channel().await.expect("a channel");
    let queue = channel
        .queue_declare(
            "".into(),
            QueueDeclareOptions {
                exclusive: true,
                ..QueueDeclareOptions::default()
            },
            FieldTable::default(),
        )
        .await
        .expect("a queue")
        .name()
        .to_string();
    channel
        .queue_bind(
            queue.as_str().into(),
            EXCHANGE.into(),
            "#".into(),
            QueueBindOptions::default(),
            FieldTable::default(),
        )
        .await
        .expect("a binding");
    (connection, channel, queue)
}

/// The test's messages in the queue as (routing key, n), skipping other tests' messages.
async fn received(channel: &Channel, queue: &str, marker: Uuid) -> Vec<(String, i64)> {
    let mut seen = Vec::new();
    while let Some(message) = channel
        .basic_get(queue.into(), BasicGetOptions { no_ack: true })
        .await
        .expect("a get")
    {
        let delivery = message.delivery;
        assert_eq!(delivery.properties.delivery_mode(), &Some(2));
        let body: Value = serde_json::from_slice(&delivery.data).expect("a JSON body");
        if body.get("marker") == Some(&json!(marker)) {
            seen.push((
                delivery.routing_key.to_string(),
                body.get("n").and_then(Value::as_i64).expect("a number"),
            ));
        }
    }
    seen
}

#[tokio::test]
async fn confirmed_messages_reach_a_queue_in_order_and_leave_the_outbox() {
    let pool = TestPool::new().await;
    let publisher = Publisher::connect(&broker(), Duration::from_secs(5), EXCHANGE)
        .await
        .expect("a publisher");
    let (_connection, channel, queue) = subscribe().await;
    let marker = Uuid::now_v7();
    write(
        &pool,
        marker,
        &["seats.held", "seats.sold", "seats.released"],
    )
    .await;

    let count = Relay::new(pool.clone(), broker(), settings(100))
        .publish_batch(&publisher)
        .await
        .expect("a published batch");

    assert_eq!(count, 3);
    assert_eq!(outbox_size(&pool).await, 0);
    assert_eq!(
        received(&channel, &queue, marker).await,
        [
            ("seats.held".to_owned(), 0),
            ("seats.sold".to_owned(), 1),
            ("seats.released".to_owned(), 2)
        ]
    );
}

#[tokio::test]
async fn a_batch_takes_the_oldest_rows() {
    let pool = TestPool::new().await;
    let publisher = Publisher::connect(&broker(), Duration::from_secs(5), EXCHANGE)
        .await
        .expect("a publisher");
    let (_connection, channel, queue) = subscribe().await;
    let marker = Uuid::now_v7();
    write(
        &pool,
        marker,
        &["seats.held", "seats.sold", "seats.released"],
    )
    .await;

    let count = Relay::new(pool.clone(), broker(), settings(2))
        .publish_batch(&publisher)
        .await
        .expect("a published batch");

    assert_eq!(count, 2);
    assert_eq!(outbox_size(&pool).await, 1);
    let numbers: Vec<i64> = received(&channel, &queue, marker)
        .await
        .into_iter()
        .map(|(_, n)| n)
        .collect();
    assert_eq!(numbers, [0, 1]);
}

#[tokio::test]
async fn rows_stay_when_the_broker_is_gone() {
    let pool = TestPool::new().await;
    let publisher = Publisher::connect(&broker(), Duration::from_secs(5), EXCHANGE)
        .await
        .expect("a publisher");
    write(&pool, Uuid::now_v7(), &["seats.held", "seats.sold"]).await;
    publisher.close().await;

    let result = Relay::new(pool.clone(), broker(), settings(100))
        .publish_batch(&publisher)
        .await;

    assert!(result.is_err());
    assert_eq!(outbox_size(&pool).await, 2);
}
