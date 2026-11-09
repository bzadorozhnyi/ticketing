mod common;

use std::time::{Duration, Instant};

use axum::Router;
use axum::http::StatusCode;
use axum::http::header::AUTHORIZATION;
use axum::routing::any;
use common::app::{gateway_for, spawn_gateway, test_settings};
use common::client::{authorization, client, get};
use common::upstream::{answering, spawn_upstream};

const SLOW: Duration = Duration::from_millis(300);

/// Sends `count` requests at once and returns their statuses and the error codes.
async fn at_once(gateway: &str, path: &str, count: usize) -> Vec<(StatusCode, String)> {
    let requests: Vec<_> = (0..count)
        .map(|_| {
            let url = format!("{gateway}{path}");
            tokio::spawn(async move {
                let response = client().get(url).send().await.expect("a response");
                let status = response.status();
                (status, response.text().await.unwrap_or_default())
            })
        })
        .collect();
    let mut answers = Vec::new();
    for request in requests {
        answers.push(request.await.expect("a finished request"));
    }
    answers
}

fn rejected(answers: &[(StatusCode, String)]) -> usize {
    answers
        .iter()
        .filter(|(status, body)| {
            *status == StatusCode::SERVICE_UNAVAILABLE && body.contains(r#""code":"bulkhead_full""#)
        })
        .count()
}

#[tokio::test]
async fn a_refusal_tells_the_client_when_to_come_back() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, SLOW), |s| {
        s.bulkhead.max_concurrent = 1;
    })
    .await;
    let first = {
        let gateway = gateway.clone();
        tokio::spawn(async move { get(&gateway).await })
    };
    tokio::time::sleep(SLOW / 3).await;

    let refused = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(refused.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(refused.headers()["retry-after"], "1");
    assert_eq!(first.await.unwrap(), StatusCode::OK);
}

#[tokio::test]
async fn a_request_over_the_limit_is_refused_at_once_without_reaching_the_upstream() {
    let (gateway, upstream) = gateway_for(answering(StatusCode::OK, SLOW), |s| {
        s.bulkhead.max_concurrent = 2;
    })
    .await;

    let started = Instant::now();
    let answers = at_once(&gateway, "/api/v1/events", 3).await;

    assert_eq!(rejected(&answers), 1, "{answers:?}");
    assert_eq!(
        answers.iter().filter(|(s, _)| *s == StatusCode::OK).count(),
        2
    );
    assert_eq!(upstream.received().len(), 2);
    assert!(started.elapsed() < SLOW * 2);
}

#[tokio::test]
async fn an_answer_frees_its_place() {
    let (gateway, upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        s.bulkhead.max_concurrent = 1;
    })
    .await;

    for _ in 0..5 {
        assert_eq!(get(&gateway).await, StatusCode::OK);
    }

    assert_eq!(upstream.received().len(), 5);
}

#[tokio::test]
async fn a_client_that_goes_away_frees_its_place() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, SLOW), |s| {
        s.bulkhead.max_concurrent = 1;
    })
    .await;
    let impatient = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_millis(50))
        .build()
        .unwrap();
    let abandoned = impatient
        .get(format!("{gateway}/api/v1/events"))
        .header(AUTHORIZATION, authorization())
        .send()
        .await;
    assert!(abandoned.is_err());
    tokio::time::sleep(Duration::from_millis(50)).await;

    assert_eq!(get(&gateway).await, StatusCode::OK);
}

#[tokio::test]
async fn retries_keep_their_one_place() {
    let routes = answering(StatusCode::SERVICE_UNAVAILABLE, Duration::ZERO);
    let (gateway, upstream) = gateway_for(routes, |s| {
        s.bulkhead.max_concurrent = 1;
        s.retry.base_delay = Duration::from_millis(400);
    })
    .await;

    let retrying = {
        let gateway = gateway.clone();
        tokio::spawn(async move { get(&gateway).await })
    };
    tokio::time::sleep(Duration::from_millis(30)).await;
    let answers = at_once(&gateway, "/api/v1/events", 1).await;

    assert_eq!(rejected(&answers), 1, "{answers:?}");
    assert_eq!(retrying.await.unwrap(), StatusCode::SERVICE_UNAVAILABLE);
    assert!(
        upstream.received().len() >= 2,
        "the first request kept retrying"
    );
}

#[tokio::test]
async fn a_full_catalog_does_not_block_seat_hold() {
    let catalog = spawn_upstream(answering(StatusCode::OK, SLOW)).await;
    let holds = Router::new().route("/api/v1/holds/{*rest}", any(async || StatusCode::OK));
    let seat_hold = spawn_upstream(holds).await;
    let mut settings = test_settings(&catalog.url);
    settings.upstreams.seat_hold_url = seat_hold.url.parse().unwrap();
    settings.bulkhead.max_concurrent = 2;
    let gateway = spawn_gateway(&settings).await;

    let busy = {
        let gateway = gateway.clone();
        tokio::spawn(async move { at_once(&gateway, "/api/v1/events", 3).await })
    };
    tokio::time::sleep(Duration::from_millis(30)).await;
    let holds = at_once(&gateway, "/api/v1/holds/7", 2).await;

    assert!(holds.iter().all(|(s, _)| *s == StatusCode::OK), "{holds:?}");
    assert_eq!(rejected(&busy.await.unwrap()), 1);
}

#[tokio::test]
async fn an_upstream_can_have_its_own_limit() {
    let (gateway, upstream) = gateway_for(answering(StatusCode::OK, SLOW), |s| {
        s.upstreams.catalog_max_concurrent = Some(1);
    })
    .await;

    let answers = at_once(&gateway, "/api/v1/events", 3).await;

    assert_eq!(rejected(&answers), 2, "{answers:?}");
    assert_eq!(upstream.received().len(), 1);
}
