//! A user who asks again for seats they hold, because the first answer was lost: the
//! refusal names their holds, so the seats are not locked away from their own owner.

mod common;

use axum::http::StatusCode;
use common::db::TestPool;
use common::factories::hold_factory;
use common::service::{client, spawn_service, spawn_service_with};
use seat_hold::core::settings::HoldSettings;
use serde_json::{Value, json};
use uuid::Uuid;

const USER: &str = "user-1";

async fn post_hold(
    service: &str,
    event_id: Uuid,
    seat_ids: &[Uuid],
    key: Option<&str>,
) -> (StatusCode, Value) {
    let mut request = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", USER)
        .json(&json!({"event_id": event_id, "seat_ids": seat_ids}));
    if let Some(key) = key {
        request = request.header("idempotency-key", key);
    }
    let response = request.send().await.expect("a service response");
    let status = response.status();
    (status, response.json().await.expect("a JSON body"))
}

#[tokio::test]
async fn a_repeat_without_a_key_names_the_hold_the_user_already_has() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let (_, first) = post_hold(&service, event_id, &[seat_id], None).await;

    let (status, body) = post_hold(&service, event_id, &[seat_id], None).await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["code"], "already_held_by_you");
    assert_eq!(body["details"], json!([first["hold_id"]]));
}

#[tokio::test]
async fn a_repeat_at_the_users_limit_still_names_the_hold() {
    let pool = TestPool::new().await;
    let settings = HoldSettings {
        max_seats_per_user: 1,
        ..HoldSettings::default()
    };
    let service = spawn_service_with(pool.clone(), settings).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let (_, first) = post_hold(&service, event_id, &[seat_id], None).await;

    let (status, body) = post_hold(&service, event_id, &[seat_id], None).await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["code"], "already_held_by_you");
    assert_eq!(body["details"], json!([first["hold_id"]]));
}

#[tokio::test]
async fn a_seat_of_the_user_beside_a_free_one_names_the_users_hold_and_takes_nothing() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let mine = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .call()
        .await;
    let free = Uuid::now_v7();

    let (status, body) = post_hold(&service, mine.event_id, &[mine.seat_id, free], None).await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["code"], "already_held_by_you");
    assert_eq!(body["details"], json!([mine.hold_id]));
    let (status, _) = post_hold(&service, mine.event_id, &[free], None).await;
    assert_eq!(status, StatusCode::CREATED, "the free seat was not taken");
}

#[tokio::test]
async fn seats_of_two_holds_of_the_user_name_both() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let event_id = Uuid::now_v7();
    let first = hold_factory()
        .pool(&pool)
        .event_id(event_id)
        .owner_id(USER.to_owned())
        .call()
        .await;
    let second = hold_factory()
        .pool(&pool)
        .event_id(event_id)
        .owner_id(USER.to_owned())
        .call()
        .await;

    let (status, body) =
        post_hold(&service, event_id, &[first.seat_id, second.seat_id], None).await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["code"], "already_held_by_you");
    let mut named: Vec<String> = serde_json::from_value(body["details"].clone()).expect("hold ids");
    named.sort();
    let mut expected = vec![first.hold_id.to_string(), second.hold_id.to_string()];
    expected.sort();
    assert_eq!(named, expected);
}

#[tokio::test]
async fn a_seat_of_another_user_is_still_taken() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let mine = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .call()
        .await;
    let theirs = hold_factory()
        .pool(&pool)
        .event_id(mine.event_id)
        .call()
        .await;

    let (status, body) = post_hold(
        &service,
        mine.event_id,
        &[mine.seat_id, theirs.seat_id],
        None,
    )
    .await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["code"], "conflict");
}

#[tokio::test]
async fn a_seat_the_user_bought_is_taken_not_held() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let bought = hold_factory()
        .pool(&pool)
        .owner_id(USER.to_owned())
        .status("confirmed")
        .call()
        .await;

    let (status, body) = post_hold(&service, bought.event_id, &[bought.seat_id], None).await;

    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(body["code"], "conflict");
}

#[tokio::test]
async fn a_repeat_with_the_key_still_replays_the_first_answer() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let (event_id, seat_id) = (Uuid::now_v7(), Uuid::now_v7());
    let (_, first) = post_hold(&service, event_id, &[seat_id], Some("buy-1")).await;

    let (status, body) = post_hold(&service, event_id, &[seat_id], Some("buy-1")).await;

    assert_eq!(status, StatusCode::CREATED);
    assert_eq!(body["hold_id"], first["hold_id"]);
}
