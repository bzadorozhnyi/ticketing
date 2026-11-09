mod common;

use std::time::{Duration, Instant};

use axum::http::StatusCode;
use axum::http::header::AUTHORIZATION;
use common::app::{JWT_SECRET, gateway_for, spawn_gateway, test_settings};
use common::client::anonymous_client;
use common::upstream::{answering, spawn_upstream};
use gateway::auth::issue_token;
use gateway::settings::Settings;
use tokio::io::AsyncReadExt;
use tokio::net::TcpListener;

const VALKEY: &str = "redis://127.0.0.1:6379";

fn limited(settings: &mut Settings, per_second: f64, burst: u32) {
    settings.rate_limit.valkey_url = Some(VALKEY.to_owned());
    settings.rate_limit.per_second = per_second;
    settings.rate_limit.burst = burst;
}

/// A user no other test uses, so tests never share a bucket in Valkey.
fn fresh_user() -> String {
    format!("rate-test-{:x}", rand::random::<u64>())
}

async fn get_as(gateway: &str, user: &str) -> reqwest::Response {
    let token = issue_token(JWT_SECRET, user, Duration::from_secs(60)).expect("a token");
    anonymous_client()
        .get(format!("{gateway}/api/v1/events"))
        .header(AUTHORIZATION, format!("Bearer {token}"))
        .send()
        .await
        .expect("a response")
}

async fn status_as(gateway: &str, user: &str) -> StatusCode {
    get_as(gateway, user).await.status()
}

#[tokio::test]
async fn a_user_over_the_burst_gets_429_with_retry_after() {
    let (gateway, upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 0.5, 3);
    })
    .await;
    let user = fresh_user();
    for _ in 0..3 {
        assert_eq!(status_as(&gateway, &user).await, StatusCode::OK);
    }

    let refused = get_as(&gateway, &user).await;

    assert_eq!(refused.status(), StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(refused.headers()["retry-after"], "2", "one token per 2 s");
    let body = refused.text().await.unwrap();
    assert!(body.contains(r#""code":"rate_limited""#), "{body}");
    assert_eq!(upstream.received().len(), 3);
}

#[tokio::test]
async fn tokens_come_back_at_the_rate() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 20.0, 1);
    })
    .await;
    let user = fresh_user();
    assert_eq!(status_as(&gateway, &user).await, StatusCode::OK);
    assert_eq!(
        status_as(&gateway, &user).await,
        StatusCode::TOO_MANY_REQUESTS
    );

    tokio::time::sleep(Duration::from_millis(80)).await;

    assert_eq!(status_as(&gateway, &user).await, StatusCode::OK);
}

#[tokio::test]
async fn a_quiet_user_saves_up_no_more_than_the_burst() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 20.0, 2);
    })
    .await;
    let user = fresh_user();
    status_as(&gateway, &user).await;

    tokio::time::sleep(Duration::from_millis(300)).await;
    let mut statuses = Vec::new();
    for _ in 0..5 {
        statuses.push(status_as(&gateway, &user).await);
    }

    let passed = statuses.iter().filter(|s| **s == StatusCode::OK).count();
    assert_eq!(
        passed, 2,
        "300 ms earn 6 tokens, the bucket holds 2: {statuses:?}"
    );
}

#[tokio::test]
async fn one_users_limit_does_not_touch_another() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 0.5, 1);
    })
    .await;
    let busy = fresh_user();
    status_as(&gateway, &busy).await;
    assert_eq!(
        status_as(&gateway, &busy).await,
        StatusCode::TOO_MANY_REQUESTS
    );

    assert_eq!(status_as(&gateway, &fresh_user()).await, StatusCode::OK);
}

#[tokio::test]
async fn two_gateways_share_one_limit() {
    let upstream = spawn_upstream(answering(StatusCode::OK, Duration::ZERO)).await;
    let mut settings = test_settings(&upstream.url);
    limited(&mut settings, 0.5, 4);
    let first = spawn_gateway(&settings).await;
    let second = spawn_gateway(&settings).await;
    let user = fresh_user();

    let mut statuses = Vec::new();
    for gateway in [&first, &second, &first, &second, &first, &second] {
        statuses.push(status_as(gateway, &user).await);
    }

    let passed = statuses.iter().filter(|s| **s == StatusCode::OK).count();
    assert_eq!(passed, 4, "{statuses:?}");
    assert_eq!(upstream.received().len(), 4);
}

#[tokio::test]
async fn the_replica_limits_a_user_while_valkey_is_down() {
    let (gateway, upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 0.5, 2);
        s.rate_limit.valkey_url = Some("redis://127.0.0.1:1".to_owned());
    })
    .await;
    let user = fresh_user();

    let mut statuses = Vec::new();
    for _ in 0..4 {
        statuses.push(status_as(&gateway, &user).await);
    }

    assert_eq!(
        statuses,
        [
            StatusCode::OK,
            StatusCode::OK,
            StatusCode::TOO_MANY_REQUESTS,
            StatusCode::TOO_MANY_REQUESTS
        ]
    );
    assert_eq!(upstream.received().len(), 2);
}

/// A Valkey that reads a command and never answers: every call to it costs the timeout.
async fn spawn_hanging_valkey() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("a free port");
    let address = listener.local_addr().expect("a bound address");
    tokio::spawn(async move {
        let mut held = Vec::new();
        while let Ok((mut stream, _)) = listener.accept().await {
            let mut buffer = [0_u8; 1024];
            let _ = stream.read(&mut buffer).await;
            held.push(stream);
        }
    });
    format!("redis://{address}")
}

#[tokio::test]
async fn a_hanging_valkey_is_given_up_on_and_then_not_asked() {
    let valkey = spawn_hanging_valkey().await;
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 100.0, 10);
        s.rate_limit.valkey_url = Some(valkey);
        s.breaker.window = 2;
        s.breaker.min_calls = 2;
        s.breaker.open_duration = Duration::from_secs(60);
    })
    .await;
    let user = fresh_user();

    for _ in 0..2 {
        let started = Instant::now();
        assert_eq!(status_as(&gateway, &user).await, StatusCode::OK);
        assert!(started.elapsed() < Duration::from_millis(500));
    }
    let started = Instant::now();
    assert_eq!(status_as(&gateway, &user).await, StatusCode::OK);

    let elapsed = started.elapsed();
    assert!(
        elapsed < Duration::from_millis(40),
        "{elapsed:?}: Valkey was asked again"
    );
}

#[tokio::test]
async fn a_user_over_the_replicas_limit_is_refused_without_asking_valkey() {
    let valkey = spawn_hanging_valkey().await;
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 0.5, 2);
        s.rate_limit.valkey_url = Some(valkey);
    })
    .await;
    let user = fresh_user();
    for _ in 0..2 {
        assert_eq!(status_as(&gateway, &user).await, StatusCode::OK);
    }
    let started = Instant::now();

    let status = status_as(&gateway, &user).await;

    let elapsed = started.elapsed();
    assert_eq!(status, StatusCode::TOO_MANY_REQUESTS);
    assert!(
        elapsed < Duration::from_millis(40),
        "{elapsed:?}: Valkey was asked"
    );
}

#[tokio::test]
async fn a_refused_request_takes_no_place_in_the_bulkhead() {
    let (gateway, _upstream) =
        gateway_for(answering(StatusCode::OK, Duration::from_millis(300)), |s| {
            limited(s, 0.5, 1);
            s.bulkhead.max_concurrent = 1;
        })
        .await;
    let user = fresh_user();
    let first = {
        let gateway = gateway.clone();
        let user = user.clone();
        tokio::spawn(async move { status_as(&gateway, &user).await })
    };
    tokio::time::sleep(Duration::from_millis(50)).await;

    let refused = get_as(&gateway, &user).await;

    assert_eq!(refused.status(), StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(first.await.unwrap(), StatusCode::OK);
}

#[tokio::test]
async fn a_request_without_a_token_is_refused_before_the_limit() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        limited(s, 0.5, 1);
    })
    .await;

    let response = anonymous_client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}
