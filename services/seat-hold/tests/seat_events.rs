mod common;

use axum::http::StatusCode;
use common::db::TestPool;
use common::factories::hold_factory;
use common::service::{client, spawn_service};
use reqwest::Response;
use seat_hold::core::db::DbPool;
use serde_json::{Value, json};
use uuid::Uuid;

const USER: &str = "user-1";

/// The outbox in the order the relay publishes it: (routing key, event).
async fn outbox(pool: &TestPool) -> Vec<(String, Value)> {
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query_as("SELECT routing_key, body FROM outbox ORDER BY id")
        .fetch_all(&mut *conn)
        .await
        .expect("the outbox")
}

async fn version_of(pool: &TestPool, seat_id: Uuid) -> Option<i64> {
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query_scalar("SELECT version FROM holds WHERE seat_id = $1")
        .bind(seat_id)
        .fetch_one(&mut *conn)
        .await
        .expect("a stored hold")
}

async fn hold(
    service: &str,
    user: &str,
    event_id: Uuid,
    seat_ids: &[Uuid],
    key: Option<&str>,
) -> Response {
    let mut request = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", user)
        .json(&json!({"event_id": event_id, "seat_ids": seat_ids}));
    if let Some(key) = key {
        request = request.header("idempotency-key", key);
    }
    request.send().await.expect("a service response")
}

async fn internal(service: &str, hold_id: Uuid, action: &str) -> Response {
    client()
        .post(format!("{service}/internal/v1/holds/{hold_id}/{action}"))
        .header("x-user-id", USER)
        .send()
        .await
        .expect("a service response")
}

async fn release(service: &str, hold_id: Uuid) -> Response {
    client()
        .delete(format!("{service}/api/v1/holds/{hold_id}"))
        .header("x-user-id", USER)
        .send()
        .await
        .expect("a service response")
}

async fn act(service: &str, action: &str, hold_id: Uuid) -> Response {
    if action == "release" {
        release(service, hold_id).await
    } else {
        internal(service, hold_id, action).await
    }
}

fn versions(event: &Value) -> Vec<i64> {
    event["seats"]
        .as_array()
        .expect("seats in the event")
        .iter()
        .map(|seat| seat["version"].as_i64().expect("a version for each seat"))
        .collect()
}

#[tokio::test]
async fn a_hold_writes_one_event_with_each_seat_and_its_version() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let (event_id, seats) = (Uuid::now_v7(), vec![Uuid::now_v7(), Uuid::now_v7()]);

    let response = hold(&service, USER, event_id, &seats, None).await;

    assert_eq!(response.status(), StatusCode::CREATED);
    let hold_id = response.json::<Value>().await.unwrap()["hold_id"].clone();
    let events = outbox(&pool).await;
    assert_eq!(events.len(), 1);
    let (routing_key, event) = &events[0];
    assert_eq!(routing_key, "seats.held");
    assert_eq!(event["type"], "SeatsHeld");
    assert_eq!(event["event_id"], json!(event_id));
    assert_eq!(event["hold_id"], hold_id);
    for seat in event["seats"].as_array().unwrap() {
        let seat_id: Uuid = serde_json::from_value(seat["seat_id"].clone()).unwrap();
        assert!(seats.contains(&seat_id));
        assert_eq!(seat["version"].as_i64(), version_of(&pool, seat_id).await);
        assert!(seat["expires_at"].is_string());
    }
}

#[tokio::test]
async fn a_refused_hold_writes_nothing() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let taken = hold_factory().pool(&pool).call().await;

    let response = hold(&service, USER, taken.event_id, &[taken.seat_id], None).await;

    assert_eq!(response.status(), StatusCode::CONFLICT);
    assert_eq!(outbox(&pool).await.len(), 0);
}

#[tokio::test]
async fn a_repeated_hold_writes_nothing_more() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let (event_id, seat) = (Uuid::now_v7(), Uuid::now_v7());

    hold(&service, USER, event_id, &[seat], Some("key-1")).await;
    let repeat = hold(&service, USER, event_id, &[seat], Some("key-1")).await;

    assert_eq!(repeat.status(), StatusCode::CREATED);
    assert_eq!(outbox(&pool).await.len(), 1);
}

#[tokio::test]
async fn confirm_extend_and_release_write_once_each() {
    for (action, routing_key, kind) in [
        ("confirm", "seats.sold", "SeatsSold"),
        ("extend", "seats.extended", "HoldExtended"),
        ("release", "seats.released", "SeatsReleased"),
    ] {
        let pool = TestPool::new().await;
        let service = spawn_service(pool.clone()).await;
        let held = hold_factory()
            .pool(&pool)
            .owner_id(USER.to_owned())
            .call()
            .await;

        for _ in 0..2 {
            let response = act(&service, action, held.hold_id).await;
            assert!(
                response.status().is_success(),
                "{action}: {}",
                response.status()
            );
        }

        let events = outbox(&pool).await;
        assert_eq!(events.len(), 1, "{action}");
        assert_eq!(events[0].0, routing_key);
        assert_eq!(events[0].1["type"], kind);
        assert_eq!(events[0].1["hold_id"], json!(held.hold_id));
        assert_eq!(versions(&events[0].1).len(), 1, "{action}");
        assert_eq!(
            events[0].1["seats"][0]["expires_at"].is_string(),
            action == "extend"
        );
    }
}

#[tokio::test]
async fn each_change_of_a_seat_has_a_larger_version() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let (event_id, seat) = (Uuid::now_v7(), Uuid::now_v7());

    let first = hold(&service, USER, event_id, &[seat], None).await;
    let hold_id: Uuid =
        serde_json::from_value(first.json::<Value>().await.unwrap()["hold_id"].clone()).unwrap();
    internal(&service, hold_id, "extend").await;
    release(&service, hold_id).await;
    hold(&service, "user-2", event_id, &[seat], None).await;

    let seen: Vec<i64> = outbox(&pool)
        .await
        .iter()
        .flat_map(|(_, event)| versions(event))
        .collect();
    assert_eq!(seen.len(), 4);
    assert!(seen.windows(2).all(|pair| pair[0] < pair[1]), "{seen:?}");
}
