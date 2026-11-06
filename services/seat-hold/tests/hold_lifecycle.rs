mod common;

use axum::http::StatusCode;
use chrono::{DateTime, Utc};
use common::db::{TestPool, dead_pool};
use common::factories::hold_factory;
use common::service::{client, spawn_service};
use reqwest::Response;
use seat_hold::core::db::DbPool;
use seat_hold::core::settings::HoldSettings;
use seat_hold::holds::use_cases::cleanup_expired_holds::CleanupExpiredHoldsUseCase;
use serde_json::{Value, json};
use uuid::Uuid;

const USER: &str = "user-1";

async fn internal(service: &str, hold_id: Uuid, action: &str, user: Option<&str>) -> Response {
    let mut request = client().post(format!("{service}/internal/v1/holds/{hold_id}/{action}"));
    if let Some(user) = user {
        request = request.header("x-user-id", user);
    }
    request.send().await.expect("a service response")
}

async fn hold_state(pool: &TestPool, hold_id: Uuid) -> Option<(String, DateTime<Utc>)> {
    let mut conn = pool.get_conn().await.expect("a test connection");
    sqlx::query_as("SELECT status, expires_at FROM holds WHERE hold_id = $1")
        .bind(hold_id)
        .fetch_optional(&mut *conn)
        .await
        .expect("a stored hold")
}

#[tokio::test]
async fn extending_a_live_hold_gives_time_for_the_payment() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .expires_in_seconds(10.0)
        .call()
        .await;

    let response = internal(&service, held.hold_id, "extend", Some(USER)).await;

    assert_eq!(response.status(), StatusCode::OK);
    let body: Value = response.json().await.unwrap();
    let expires_at: DateTime<Utc> = body["expires_at"].as_str().unwrap().parse().unwrap();
    let left = (expires_at - Utc::now()).num_seconds();
    let expected = i64::try_from(HoldSettings::default().payment_ttl.as_secs()).unwrap();
    assert!((expected - 10..=expected).contains(&left), "{left}");
}

#[tokio::test]
async fn extending_never_shortens_a_hold() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let long = 100_000.0;
    let held = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .expires_in_seconds(long)
        .call()
        .await;
    let before = hold_state(&pool, held.hold_id).await.unwrap().1;

    internal(&service, held.hold_id, "extend", Some(USER)).await;

    assert_eq!(hold_state(&pool, held.hold_id).await.unwrap().1, before);
}

#[tokio::test]
async fn an_expired_hold_cannot_be_extended_or_confirmed() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let expired = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .expires_in_seconds(-1.0)
        .call()
        .await;

    for action in ["extend", "confirm"] {
        let response = internal(&service, expired.hold_id, action, Some(USER)).await;

        assert_eq!(response.status(), StatusCode::CONFLICT, "{action}");
        let body: Value = response.json().await.unwrap();
        assert_eq!(body["code"], "conflict");
    }
}

#[tokio::test]
async fn an_unknown_or_foreign_hold_is_refused() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let foreign = hold_factory().pool(&pool).call().await;

    for hold_id in [foreign.hold_id, Uuid::now_v7()] {
        for action in ["extend", "confirm"] {
            let response = internal(&service, hold_id, action, Some(USER)).await;

            assert_eq!(response.status(), StatusCode::CONFLICT, "{action}");
        }
    }
    assert_eq!(hold_state(&pool, foreign.hold_id).await.unwrap().0, "held");
}

#[tokio::test]
async fn confirming_is_safe_to_repeat() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .call()
        .await;

    for _ in 0..2 {
        let response = internal(&service, held.hold_id, "confirm", Some(USER)).await;

        assert_eq!(response.status(), StatusCode::OK);
    }
    assert_eq!(
        hold_state(&pool, held.hold_id).await.unwrap().0,
        "confirmed"
    );
}

#[tokio::test]
async fn a_repeat_after_the_ttl_still_succeeds_on_a_confirmed_hold() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;

    for action in ["extend", "confirm"] {
        let sold = hold_factory()
            .pool(&pool)
            .owner_id(USER.to_owned())
            .expires_in_seconds(-1.0)
            .status("confirmed")
            .call()
            .await;

        let response = internal(&service, sold.hold_id, action, Some(USER)).await;

        assert_eq!(response.status(), StatusCode::OK, "{action}");
    }
}

#[tokio::test]
async fn a_confirmed_seat_is_never_free_again() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let sold = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .expires_in_seconds(-1.0)
        .status("confirmed")
        .call()
        .await;

    let taken = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", "user-2")
        .json(&json!({"event_id": sold.event_id, "seat_ids": [sold.seat_id]}))
        .send()
        .await
        .unwrap();
    let released = client()
        .delete(format!("{service}/api/v1/holds/{}", sold.hold_id))
        .header("x-user-id", USER)
        .send()
        .await
        .unwrap();
    let cleaned = CleanupExpiredHoldsUseCase::new(pool.clone(), 100)
        .execute()
        .await
        .unwrap();

    assert_eq!(taken.status(), StatusCode::CONFLICT);
    assert_eq!(released.status(), StatusCode::NO_CONTENT);
    assert_eq!(cleaned, 0);
    assert_eq!(
        hold_state(&pool, sold.hold_id).await.unwrap().0,
        "confirmed"
    );
}

#[tokio::test]
async fn internal_routes_need_a_user() {
    let service = spawn_service(TestPool::new().await).await;

    let response = internal(&service, Uuid::now_v7(), "confirm", None).await;

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn internal_routes_return_503_when_the_database_is_down() {
    let service = spawn_service(dead_pool().await).await;

    let response = internal(&service, Uuid::now_v7(), "extend", Some(USER)).await;

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}
