mod common;

use axum::http::StatusCode;
use common::db::TestPool;
use common::factories::hold_factory;
use common::service::{client, spawn_service_with};
use reqwest::Response;
use seat_hold::core::settings::HoldSettings;
use serde_json::{Value, json};
use uuid::Uuid;

const USER: &str = "user-1";
const LIMIT: i64 = 3;

async fn service(pool: &TestPool) -> String {
    let holds = HoldSettings {
        max_seats_per_user: LIMIT,
        ..HoldSettings::default()
    };
    spawn_service_with(pool.clone(), holds).await
}

async fn hold(service: &str, user: &str, event_id: Uuid, count: usize) -> Response {
    let seat_ids: Vec<Uuid> = (0..count).map(|_| Uuid::now_v7()).collect();
    client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", user)
        .json(&json!({"event_id": event_id, "seat_ids": seat_ids}))
        .send()
        .await
        .expect("a service response")
}

async fn assert_limit_reached(response: Response) {
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body: Value = response.json().await.expect("a JSON body");
    assert_eq!(body.get("code"), Some(&json!("hold_limit_reached")));
    let message = format!("At most {LIMIT} seats can be held at once for one event");
    assert_eq!(body.get("message"), Some(&json!(message)));
}

#[tokio::test]
async fn a_user_holds_up_to_the_limit_for_one_event_and_no_more() {
    let pool = TestPool::new().await;
    let service = service(&pool).await;
    let event_id = Uuid::now_v7();

    assert_eq!(
        hold(&service, USER, event_id, 2).await.status(),
        StatusCode::CREATED
    );
    assert_limit_reached(hold(&service, USER, event_id, 2).await).await;
    assert_eq!(
        hold(&service, USER, event_id, 1).await.status(),
        StatusCode::CREATED
    );
    assert_limit_reached(hold(&service, USER, event_id, 1).await).await;
}

#[tokio::test]
async fn the_limit_is_per_event_and_per_user() {
    let pool = TestPool::new().await;
    let service = service(&pool).await;
    let event_id = Uuid::now_v7();
    hold(&service, USER, event_id, 3).await;

    let another_event = hold(&service, USER, Uuid::now_v7(), 3).await;
    let another_user = hold(&service, "user-2", event_id, 3).await;

    assert_eq!(another_event.status(), StatusCode::CREATED);
    assert_eq!(another_user.status(), StatusCode::CREATED);
}

#[tokio::test]
async fn expired_and_sold_seats_do_not_count() {
    let pool = TestPool::new().await;
    let service = service(&pool).await;
    let event_id = Uuid::now_v7();
    for (expires_in_seconds, status) in [(-1.0, "held"), (300.0, "confirmed")] {
        for _ in 0..2 {
            hold_factory()
                .pool(&pool)
                .event_id(event_id)
                .owner_id(USER.to_owned())
                .expires_in_seconds(expires_in_seconds)
                .status(status)
                .call()
                .await;
        }
    }

    let response = hold(&service, USER, event_id, 3).await;

    assert_eq!(response.status(), StatusCode::CREATED);
}
