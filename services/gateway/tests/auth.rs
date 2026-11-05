mod common;

use axum::Router;
use axum::http::StatusCode;
use axum::routing::any;
use common::app::{JWT_SECRET, MAX_BODY_BYTES, spawn_gateway, test_settings};
use common::client::{anonymous_client, client};
use common::upstream::{Upstream, spawn_upstream};
use gateway::auth::{Claims, issue_token};
use jsonwebtoken::{Algorithm, EncodingKey, Header, encode, get_current_timestamp};
use serde::Serialize;
use std::time::Duration;

const UNAUTHORIZED_BODY: &str =
    r#"{"code":"unauthorized","message":"Authentication required","details":[]}"#;
const ALG_NONE_TOKEN: &str =
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhZG1pbiIsImV4cCI6NDEwMjQ0NDgwMH0.";

async fn gateway() -> (String, Upstream) {
    let routes = Router::new().route("/api/v1/events", any(async || "ok"));
    let upstream = spawn_upstream(routes).await;
    let gateway = spawn_gateway(&test_settings(&upstream.url)).await;
    (gateway, upstream)
}

fn signed(header: &Header, claims: &impl Serialize) -> String {
    let key = EncodingKey::from_secret(JWT_SECRET.as_bytes());
    encode(header, claims, &key).expect("a signed token")
}

async fn assert_rejected(authorization: &str) {
    let (gateway, upstream) = gateway().await;

    let response = anonymous_client()
        .get(format!("{gateway}/api/v1/events"))
        .header("authorization", authorization)
        .send()
        .await
        .expect("a gateway response");

    assert_eq!(
        response.status(),
        StatusCode::UNAUTHORIZED,
        "{authorization}"
    );
    assert_eq!(response.headers()["www-authenticate"], "Bearer");
    assert_eq!(
        response.text().await.expect("a body"),
        UNAUTHORIZED_BODY,
        "{authorization}"
    );
    assert!(upstream.received().is_empty(), "{authorization}");
}

#[tokio::test]
async fn rejects_a_request_without_a_token() {
    let (gateway, upstream) = gateway().await;

    let response = anonymous_client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(response.headers()["www-authenticate"], "Bearer");
    assert_eq!(response.text().await.unwrap(), UNAUTHORIZED_BODY);
    assert!(upstream.received().is_empty());
}

#[tokio::test]
async fn rejects_malformed_credentials() {
    assert_rejected("Bearer not-a-token").await;
    assert_rejected("Bearer").await;
    assert_rejected("Basic dXNlcjpwYXNz").await;
}

#[tokio::test]
async fn rejects_a_valid_token_under_another_scheme() {
    let token = issue_token(JWT_SECRET, "user-1", Duration::from_secs(60)).unwrap();

    assert_rejected(&format!("Basic {token}")).await;
}

#[tokio::test]
async fn rejects_a_token_signed_with_another_secret() {
    let secret = "another-secret-with-at-least-32-bytes";
    let token = issue_token(secret, "user-1", Duration::from_secs(60)).unwrap();

    assert_rejected(&format!("Bearer {token}")).await;
}

#[tokio::test]
async fn rejects_a_token_that_has_just_expired() {
    let claims = Claims {
        sub: "user-1".to_owned(),
        exp: get_current_timestamp() - 1,
    };
    let token = signed(&Header::new(Algorithm::HS256), &claims);

    assert_rejected(&format!("Bearer {token}")).await;
}

#[tokio::test]
async fn rejects_an_unsigned_token() {
    assert_rejected(&format!("Bearer {ALG_NONE_TOKEN}")).await;
}

#[tokio::test]
async fn rejects_a_token_with_another_algorithm() {
    let claims = Claims {
        sub: "user-1".to_owned(),
        exp: get_current_timestamp() + 60,
    };
    let token = signed(&Header::new(Algorithm::HS384), &claims);

    assert_rejected(&format!("Bearer {token}")).await;
}

#[tokio::test]
async fn rejects_a_token_without_a_user() {
    #[derive(Serialize)]
    struct NoSubject {
        exp: u64,
    }
    let claims = NoSubject {
        exp: get_current_timestamp() + 60,
    };
    let token = signed(&Header::new(Algorithm::HS256), &claims);

    assert_rejected(&format!("Bearer {token}")).await;
}

#[tokio::test]
async fn rejects_a_token_without_an_expiry() {
    #[derive(Serialize)]
    struct NoExpiry {
        sub: String,
    }
    let claims = NoExpiry {
        sub: "user-1".to_owned(),
    };
    let token = signed(&Header::new(Algorithm::HS256), &claims);

    assert_rejected(&format!("Bearer {token}")).await;
}

#[tokio::test]
async fn refuses_an_anonymous_body_before_reading_it() {
    let (gateway, upstream) = gateway().await;

    let response = anonymous_client()
        .post(format!("{gateway}/api/v1/events"))
        .body("x".repeat(MAX_BODY_BYTES + 1))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(upstream.received().is_empty());
}

#[tokio::test]
async fn passes_the_user_from_the_token_and_not_from_the_client() {
    let (gateway, upstream) = gateway().await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .header("x-user-id", "admin")
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let received = upstream.received();
    let values: Vec<_> = received[0].headers.get_all("x-user-id").iter().collect();
    assert_eq!(values, ["user-1"]);
}

#[tokio::test]
async fn does_not_forward_the_token_upstream() {
    let (gateway, upstream) = gateway().await;

    client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert!(!upstream.received()[0].headers.contains_key("authorization"));
}

#[tokio::test]
async fn accepts_the_scheme_in_any_case() {
    let (gateway, _upstream) = gateway().await;
    let token = issue_token(JWT_SECRET, "user-1", Duration::from_secs(60)).unwrap();

    let response = anonymous_client()
        .get(format!("{gateway}/api/v1/events"))
        .header("authorization", format!("bearer {token}"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
}
