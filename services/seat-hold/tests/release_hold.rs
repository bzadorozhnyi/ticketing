mod common;

use axum::http::StatusCode;
use common::db::{TestPool, dead_pool};
use common::factories::hold_factory;
use common::service::{client, spawn_service};
use reqwest::Response;
use seat_hold::core::db::DbPool;
use serde_json::{Value, json};
use uuid::Uuid;

const USER: &str = "user-1";

async fn delete_hold(service: &str, user: Option<&str>, hold_id: &str) -> Response {
    let mut request = client().delete(format!("{service}/api/v1/holds/{hold_id}"));
    if let Some(user) = user {
        request = request.header("x-user-id", user);
    }
    request.send().await.expect("a service response")
}

async fn count_holds(pool: &TestPool, hold_id: Uuid) -> i64 {
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query_scalar("SELECT count(*) FROM holds WHERE hold_id = $1")
        .bind(hold_id)
        .fetch_one(&mut *conn)
        .await
        .expect("a count")
}

#[tokio::test]
async fn releases_every_seat_of_the_hold_and_frees_them() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let hold_id = Uuid::now_v7();
    let event_id = Uuid::now_v7();
    let first = hold_factory()
        .pool(&pool)
        .event_id(event_id)
        .hold_id(hold_id)
        .owner_id(USER.to_owned())
        .call()
        .await;
    hold_factory()
        .pool(&pool)
        .event_id(event_id)
        .hold_id(hold_id)
        .owner_id(USER.to_owned())
        .call()
        .await;

    let response = delete_hold(&service, Some(USER), &hold_id.to_string()).await;

    assert_eq!(response.status(), StatusCode::NO_CONTENT);
    assert_eq!(count_holds(&pool, hold_id).await, 0);

    let retaken = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", "user-2")
        .json(&json!({"event_id": event_id, "seat_ids": [first.seat_id]}))
        .send()
        .await
        .unwrap();
    assert_eq!(retaken.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn leaves_other_holds_alone() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let mine = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .call()
        .await;
    let other = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .call()
        .await;

    delete_hold(&service, Some(USER), &mine.hold_id.to_string()).await;

    assert_eq!(count_holds(&pool, other.hold_id).await, 1);
}

#[tokio::test]
async fn repeating_a_release_succeeds() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .call()
        .await;

    for _ in 0..2 {
        let response = delete_hold(&service, Some(USER), &held.hold_id.to_string()).await;

        assert_eq!(response.status(), StatusCode::NO_CONTENT);
    }
}

#[tokio::test]
async fn does_not_release_the_hold_of_another_user() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory().pool(&pool).call().await;

    let response = delete_hold(&service, Some(USER), &held.hold_id.to_string()).await;

    assert_eq!(response.status(), StatusCode::NO_CONTENT);
    assert_eq!(count_holds(&pool, held.hold_id).await, 1);
}

#[tokio::test]
async fn refuses_a_request_without_a_user() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory().pool(&pool).call().await;

    let response = delete_hold(&service, None, &held.hold_id.to_string()).await;

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(count_holds(&pool, held.hold_id).await, 1);
}

#[tokio::test]
async fn refuses_a_malformed_id() {
    let service = spawn_service(TestPool::new().await).await;

    let response = delete_hold(&service, Some(USER), "not-a-uuid").await;

    assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["code"], "validation_error");
}

#[tokio::test]
async fn returns_503_when_the_database_is_down() {
    let service = spawn_service(dead_pool().await).await;

    let response = delete_hold(&service, Some(USER), &Uuid::now_v7().to_string()).await;

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}
