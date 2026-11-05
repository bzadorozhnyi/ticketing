mod common;

use std::time::Duration;

use axum::http::StatusCode;
use chrono::{DateTime, Utc};
use common::db::{TestPool, dead_pool};
use common::factories::hold_factory;
use common::service::{client, spawn_service, spawn_service_with};
use reqwest::Response;
use seat_hold::core::db::DbPool;
use seat_hold::core::settings::HoldSettings;
use serde_json::{Value, json};
use uuid::Uuid;

const USER: &str = "user-1";

async fn post_hold(service: &str, user: Option<&str>, body: &Value) -> Response {
    let mut request = client().post(format!("{service}/api/v1/holds")).json(body);
    if let Some(user) = user {
        request = request.header("x-user-id", user);
    }
    request.send().await.expect("a service response")
}

fn hold_body(event_id: Uuid, seat_ids: &[Uuid]) -> Value {
    json!({"event_id": event_id, "seat_ids": seat_ids})
}

fn seats(count: usize) -> Vec<Uuid> {
    (0..count).map(|_| Uuid::now_v7()).collect()
}

async fn stored(pool: &TestPool, event_id: Uuid) -> Vec<(Uuid, String)> {
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query_as("SELECT seat_id, owner_id FROM holds WHERE event_id = $1 ORDER BY seat_id")
        .bind(event_id)
        .fetch_all(&mut *conn)
        .await
        .expect("stored holds")
}

async fn assert_validation_error(response: Response) {
    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let body: Value = response.json().await.expect("a JSON body");
    assert_eq!(body.get("code"), Some(&json!("validation_error")));
    assert_eq!(body.get("details"), Some(&json!([])));
}

#[tokio::test]
async fn holds_free_seats() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let event_id = Uuid::now_v7();
    let seat_ids = seats(3);

    let response = post_hold(&service, Some(USER), &hold_body(event_id, &seat_ids)).await;

    assert_eq!(response.status(), StatusCode::CREATED);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["event_id"], json!(event_id));
    assert_eq!(body["seat_ids"], json!(seat_ids));
    assert!(body["hold_id"].as_str().unwrap().parse::<Uuid>().is_ok());

    let rows = stored(&pool, event_id).await;
    assert_eq!(rows.len(), 3);
    assert!(rows.iter().all(|(_, owner)| owner == USER));
}

#[tokio::test]
async fn the_hold_ends_after_the_configured_ttl() {
    let pool = TestPool::new().await;
    let holds = HoldSettings {
        ttl: Duration::from_secs(60),
        ..HoldSettings::default()
    };
    let service = spawn_service_with(pool, holds).await;

    let response = post_hold(&service, Some(USER), &hold_body(Uuid::now_v7(), &seats(1))).await;

    let body: Value = response.json().await.unwrap();
    let expires_at: DateTime<Utc> = body["expires_at"].as_str().unwrap().parse().unwrap();
    let seconds_left = (expires_at - Utc::now()).num_seconds();
    assert!((50..=60).contains(&seconds_left), "{seconds_left}");
}

#[tokio::test]
async fn refuses_a_seat_with_an_active_hold() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory().pool(&pool).call().await;

    let response = post_hold(
        &service,
        Some(USER),
        &hold_body(held.event_id, &[held.seat_id]),
    )
    .await;

    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["code"], "conflict");
    assert_eq!(body["details"], json!([held.seat_id]));
    assert_eq!(
        stored(&pool, held.event_id).await,
        [(held.seat_id, "someone-else".to_owned())]
    );
}

#[tokio::test]
async fn takes_nothing_if_any_seat_is_taken() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory().pool(&pool).call().await;
    let free = seats(2);
    let requested = [free[0], held.seat_id, free[1]];

    let response = post_hold(&service, Some(USER), &hold_body(held.event_id, &requested)).await;

    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["details"], json!([held.seat_id]));
    assert_eq!(stored(&pool, held.event_id).await.len(), 1);

    let retry = post_hold(&service, Some(USER), &hold_body(held.event_id, &free)).await;
    assert_eq!(retry.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn takes_over_an_expired_hold() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let expired = hold_factory()
        .pool(&pool)
        .expires_in_seconds(-1.0)
        .call()
        .await;

    let response = post_hold(
        &service,
        Some(USER),
        &hold_body(expired.event_id, &[expired.seat_id]),
    )
    .await;

    assert_eq!(response.status(), StatusCode::CREATED);
    assert_eq!(
        stored(&pool, expired.event_id).await,
        [(expired.seat_id, USER.to_owned())]
    );
}

#[tokio::test]
async fn the_same_seat_is_free_in_another_event() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory().pool(&pool).call().await;

    let response = post_hold(
        &service,
        Some(USER),
        &hold_body(Uuid::now_v7(), &[held.seat_id]),
    )
    .await;

    assert_eq!(response.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn accepts_the_maximum_number_of_seats_and_refuses_one_more() {
    let pool = TestPool::new().await;
    let holds = HoldSettings {
        max_seats: 2,
        ..HoldSettings::default()
    };
    let service = spawn_service_with(pool, holds).await;

    let at_limit = post_hold(&service, Some(USER), &hold_body(Uuid::now_v7(), &seats(2))).await;
    let over_limit = post_hold(&service, Some(USER), &hold_body(Uuid::now_v7(), &seats(3))).await;

    assert_eq!(at_limit.status(), StatusCode::CREATED);
    assert_validation_error(over_limit).await;
}

#[tokio::test]
async fn refuses_an_empty_list_of_seats() {
    let service = spawn_service(TestPool::new().await).await;

    let response = post_hold(&service, Some(USER), &hold_body(Uuid::now_v7(), &[])).await;

    assert_validation_error(response).await;
}

#[tokio::test]
async fn refuses_a_duplicated_seat() {
    let service = spawn_service(TestPool::new().await).await;
    let seat = Uuid::now_v7();

    let response = post_hold(
        &service,
        Some(USER),
        &hold_body(Uuid::now_v7(), &[seat, seat]),
    )
    .await;

    assert_validation_error(response).await;
}

#[tokio::test]
async fn refuses_a_malformed_body() {
    let service = spawn_service(TestPool::new().await).await;

    let response = post_hold(&service, Some(USER), &json!({"event_id": "not-a-uuid"})).await;

    assert_validation_error(response).await;
}

#[tokio::test]
async fn refuses_a_body_over_the_limit() {
    let service = spawn_service(TestPool::new().await).await;
    let body = json!({
        "event_id": Uuid::now_v7(),
        "seat_ids": seats(1),
        "padding": "x".repeat(70 * 1024),
    });

    let response = post_hold(&service, Some(USER), &body).await;

    assert_validation_error(response).await;
}

#[tokio::test]
async fn refuses_a_request_without_a_user() {
    let service = spawn_service(TestPool::new().await).await;
    let body = hold_body(Uuid::now_v7(), &seats(1));

    for user in [None, Some("")] {
        let response = post_hold(&service, user, &body).await;

        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        let body: Value = response.json().await.unwrap();
        assert_eq!(body["code"], "unauthorized");
    }
}

#[tokio::test]
async fn returns_503_when_the_database_is_down() {
    let service = spawn_service(dead_pool().await).await;

    let response = post_hold(&service, Some(USER), &hold_body(Uuid::now_v7(), &seats(1))).await;

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["code"], "service_unavailable");
}
