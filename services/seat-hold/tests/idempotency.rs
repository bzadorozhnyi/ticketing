mod common;

use std::time::Duration;

use axum::http::StatusCode;
use common::db::TestPool;
use common::factories::hold_factory;
use common::service::{client, spawn_service};
use reqwest::Response;
use seat_hold::core::db::DbPool;
use seat_hold::holds::use_cases::cleanup_expired_holds::CleanupExpiredHoldsUseCase;
use serde_json::{Value, json};
use uuid::Uuid;

const USER: &str = "user-1";
const KEY: &str = "key-1";

async fn hold(service: &str, user: &str, key: Option<&str>, body: &Value) -> Response {
    let mut request = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", user)
        .json(body);
    if let Some(key) = key {
        request = request.header("idempotency-key", key);
    }
    request.send().await.expect("a service response")
}

fn seats_body(event_id: Uuid, seat_ids: &[Uuid]) -> Value {
    json!({"event_id": event_id, "seat_ids": seat_ids})
}

fn new_body() -> Value {
    seats_body(Uuid::now_v7(), &[Uuid::now_v7(), Uuid::now_v7()])
}

async fn count_keys(pool: &TestPool, key: &str) -> i64 {
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query_scalar("SELECT count(*) FROM hold_requests WHERE idempotency_key = $1")
        .bind(key)
        .fetch_one(&mut *conn)
        .await
        .expect("a count")
}

#[tokio::test]
async fn a_repeat_returns_the_first_result() {
    let service = spawn_service(TestPool::new().await).await;
    let body = new_body();

    let first = hold(&service, USER, Some(KEY), &body).await;
    let second = hold(&service, USER, Some(KEY), &body).await;

    assert_eq!(first.status(), StatusCode::CREATED);
    assert_eq!(second.status(), StatusCode::CREATED);
    let first: Value = first.json().await.unwrap();
    let second: Value = second.json().await.unwrap();
    assert_eq!(first, second);
}

#[tokio::test]
async fn a_repeat_without_a_key_is_a_conflict_with_itself() {
    let service = spawn_service(TestPool::new().await).await;
    let body = new_body();

    hold(&service, USER, None, &body).await;
    let repeat = hold(&service, USER, None, &body).await;

    assert_eq!(repeat.status(), StatusCode::CONFLICT);
}

#[tokio::test]
async fn the_order_of_seats_does_not_matter_on_a_repeat() {
    let service = spawn_service(TestPool::new().await).await;
    let (event_id, seat_a, seat_b) = (Uuid::now_v7(), Uuid::now_v7(), Uuid::now_v7());

    hold(
        &service,
        USER,
        Some(KEY),
        &seats_body(event_id, &[seat_a, seat_b]),
    )
    .await;
    let repeat = hold(
        &service,
        USER,
        Some(KEY),
        &seats_body(event_id, &[seat_b, seat_a]),
    )
    .await;

    assert_eq!(repeat.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn the_same_key_with_another_request_is_refused() {
    let service = spawn_service(TestPool::new().await).await;
    let body = new_body();
    hold(&service, USER, Some(KEY), &body).await;

    let other_seats = seats_body(
        Uuid::parse_str(body["event_id"].as_str().unwrap()).unwrap(),
        &[Uuid::now_v7()],
    );
    let other_event = json!({"event_id": Uuid::now_v7(), "seat_ids": body["seat_ids"]});

    for other in [other_seats, other_event] {
        let response = hold(&service, USER, Some(KEY), &other).await;

        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    }
}

#[tokio::test]
async fn keys_of_different_users_do_not_meet() {
    let service = spawn_service(TestPool::new().await).await;

    let (body, other_body) = (new_body(), new_body());

    let first: Value = hold(&service, USER, Some(KEY), &body)
        .await
        .json()
        .await
        .unwrap();
    let second: Value = hold(&service, "user-2", Some(KEY), &other_body)
        .await
        .json()
        .await
        .unwrap();
    assert_ne!(first["hold_id"], second["hold_id"]);

    for (user, body, expected) in [(USER, &body, &first), ("user-2", &other_body, &second)] {
        let repeat: Value = hold(&service, user, Some(KEY), body)
            .await
            .json()
            .await
            .unwrap();

        assert_eq!(&repeat, expected);
    }
}

#[tokio::test]
async fn a_refused_request_can_be_tried_again_with_the_same_key() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory().pool(&pool).call().await;
    let body = seats_body(held.event_id, &[held.seat_id]);

    let refused = hold(&service, USER, Some(KEY), &body).await;
    assert_eq!(refused.status(), StatusCode::CONFLICT);
    assert_eq!(count_keys(&pool, KEY).await, 0);

    client()
        .delete(format!("{service}/api/v1/holds/{}", held.hold_id))
        .header("x-user-id", "someone-else")
        .send()
        .await
        .unwrap();
    let retried = hold(&service, USER, Some(KEY), &body).await;

    assert_eq!(retried.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn a_repeat_after_a_release_returns_the_first_result_and_takes_nothing() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let body = new_body();
    let first: Value = hold(&service, USER, Some(KEY), &body)
        .await
        .json()
        .await
        .unwrap();
    let hold_id = first["hold_id"].as_str().unwrap();
    client()
        .delete(format!("{service}/api/v1/holds/{hold_id}"))
        .header("x-user-id", USER)
        .send()
        .await
        .unwrap();

    let repeat = hold(&service, USER, Some(KEY), &body).await;

    assert_eq!(repeat.status(), StatusCode::CREATED);
    let mut conn = pool.get_conn().await.unwrap();
    let rows: i64 = sqlx::query_scalar("SELECT count(*) FROM holds WHERE hold_id = $1::uuid")
        .bind(hold_id)
        .fetch_one(&mut *conn)
        .await
        .unwrap();
    assert_eq!(rows, 0);
}

#[tokio::test]
async fn a_repeat_returns_the_first_result_after_another_user_took_the_seats() {
    let service = spawn_service(TestPool::new().await).await;
    let body = new_body();
    let first: Value = hold(&service, USER, Some(KEY), &body)
        .await
        .json()
        .await
        .unwrap();
    let hold_id = first["hold_id"].as_str().unwrap();
    client()
        .delete(format!("{service}/api/v1/holds/{hold_id}"))
        .header("x-user-id", USER)
        .send()
        .await
        .unwrap();
    let other = hold(&service, "user-2", None, &body).await;
    assert_eq!(other.status(), StatusCode::CREATED);

    let repeat = hold(&service, USER, Some(KEY), &body).await;

    assert_eq!(repeat.status(), StatusCode::CREATED);
    let repeat: Value = repeat.json().await.unwrap();
    assert_eq!(repeat, first);
}

#[tokio::test]
async fn an_invalid_key_is_refused() {
    let service = spawn_service(TestPool::new().await).await;
    let long = "x".repeat(256);

    for key in ["", long.as_str()] {
        let response = hold(&service, USER, Some(key), &new_body()).await;

        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    }
}

#[tokio::test]
async fn old_keys_are_removed_and_recent_ones_stay() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    hold(&service, USER, Some(KEY), &new_body()).await;
    hold(&service, USER, Some("old-key"), &new_body()).await;
    {
        let mut conn = pool.get_conn().await.unwrap();
        sqlx::query(
            "UPDATE hold_requests SET created_at = now() - interval '2 days'
             WHERE idempotency_key = 'old-key'",
        )
        .execute(&mut *conn)
        .await
        .unwrap();
    }

    CleanupExpiredHoldsUseCase::new(pool.clone(), 100, Duration::from_hours(24))
        .execute()
        .await
        .unwrap();

    assert_eq!(count_keys(&pool, "old-key").await, 0);
    assert_eq!(count_keys(&pool, KEY).await, 1);
}
