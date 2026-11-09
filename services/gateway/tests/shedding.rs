mod common;

use std::time::{Duration, Instant};

use axum::http::StatusCode;
use common::app::gateway_for;
use common::client::{anonymous_client, client};
use common::upstream::answering;

const SLOW: Duration = Duration::from_millis(300);

/// A gateway with one place for a request in flight, taken by a slow request for `SLOW`.
async fn full_gateway() -> (String, common::upstream::Upstream) {
    let (gateway, upstream) = gateway_for(answering(StatusCode::OK, SLOW), |s| {
        s.limits.max_in_flight = 1;
    })
    .await;
    let url = format!("{gateway}/api/v1/events");
    tokio::spawn(async move { client().get(url).send().await });
    tokio::time::sleep(SLOW / 3).await;
    (gateway, upstream)
}

#[tokio::test]
async fn a_request_over_the_limit_is_refused_at_once_with_retry_after() {
    let (gateway, upstream) = full_gateway().await;
    let started = Instant::now();

    let refused = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert!(started.elapsed() < SLOW / 3);
    assert_eq!(refused.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(refused.headers()["retry-after"], "1");
    let body = refused.text().await.unwrap();
    assert!(body.contains(r#""code":"overloaded""#), "{body}");
    assert_eq!(upstream.received().len(), 1);
}

#[tokio::test]
async fn the_limit_counts_requests_to_every_upstream() {
    let (gateway, _upstream) = full_gateway().await;

    let refused = client()
        .get(format!("{gateway}/api/v1/holds/any"))
        .send()
        .await
        .unwrap();

    assert_eq!(refused.status(), StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn health_routes_answer_while_the_gateway_is_full() {
    let (gateway, _upstream) = full_gateway().await;

    for path in ["/health", "/ready"] {
        let response = anonymous_client()
            .get(format!("{gateway}{path}"))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK, "{path}");
    }
}

#[tokio::test]
async fn a_full_gateway_refuses_before_checking_the_token() {
    let (gateway, _upstream) = full_gateway().await;

    let response = anonymous_client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn a_place_is_free_again_once_the_response_is_sent() {
    let (gateway, _upstream) = full_gateway().await;
    tokio::time::sleep(SLOW).await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
}
