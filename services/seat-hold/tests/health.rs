mod common;

use axum::http::StatusCode;
use common::db::{TestPool, dead_pool};
use common::service::{client, spawn_service};

#[tokio::test]
async fn health_is_ok() {
    let service = spawn_service(TestPool::new().await).await;

    let response = client()
        .get(format!("{service}/health"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.text().await.unwrap(), r#"{"status":"ok"}"#);
}

#[tokio::test]
async fn ready_is_ok_with_a_database() {
    let service = spawn_service(TestPool::new().await).await;

    let response = client()
        .get(format!("{service}/ready"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.text().await.unwrap(), r#"{"checks":{"db":"ok"}}"#);
}

#[tokio::test]
async fn ready_fails_and_health_stays_up_without_a_database() {
    let service = spawn_service(dead_pool().await).await;

    let ready = client()
        .get(format!("{service}/ready"))
        .send()
        .await
        .unwrap();
    let health = client()
        .get(format!("{service}/health"))
        .send()
        .await
        .unwrap();

    assert_eq!(ready.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        ready.text().await.unwrap(),
        r#"{"checks":{"db":"unreachable"}}"#
    );
    assert_eq!(health.status(), StatusCode::OK);
}

#[tokio::test]
async fn unknown_routes_use_the_common_error_format() {
    let service = spawn_service(TestPool::new().await).await;

    let response = client()
        .get(format!("{service}/nope"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(
        response.text().await.unwrap(),
        r#"{"code":"not_found","message":"Not found","details":[]}"#
    );
}
