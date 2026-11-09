//! Pauses every client of Valkey, so it lives in a test binary of its own:
//! the rate-limit tests would be paused too if they ran alongside.

mod common;

use std::time::{Duration, Instant};

use axum::http::StatusCode;
use axum::http::header::AUTHORIZATION;
use common::app::{JWT_SECRET, gateway_for};
use common::client::anonymous_client;
use common::upstream::answering;
use gateway::auth::issue_token;

const VALKEY: &str = "redis://127.0.0.1:6379";
const PAUSE_MS: u64 = 400;

async fn get_as(gateway: &str, user: &str) -> StatusCode {
    let token = issue_token(JWT_SECRET, user, Duration::from_secs(60)).expect("a token");
    anonymous_client()
        .get(format!("{gateway}/api/v1/events"))
        .header(AUTHORIZATION, format!("Bearer {token}"))
        .send()
        .await
        .expect("a response")
        .status()
}

#[tokio::test]
async fn a_command_that_hangs_on_an_open_connection_costs_only_the_timeout() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        s.rate_limit.valkey_url = Some(VALKEY.to_owned());
        s.rate_limit.timeout = Duration::from_millis(50);
    })
    .await;
    let user = format!("pause-test-{:x}", rand::random::<u64>());
    assert_eq!(
        get_as(&gateway, &user).await,
        StatusCode::OK,
        "opens the connection"
    );
    let client = redis::Client::open(VALKEY).unwrap();
    let mut admin = client.get_multiplexed_async_connection().await.unwrap();
    redis::cmd("CLIENT")
        .arg("PAUSE")
        .arg(PAUSE_MS)
        .arg("ALL")
        .query_async::<()>(&mut admin)
        .await
        .unwrap();

    let started = Instant::now();
    let status = get_as(&gateway, &user).await;

    let elapsed = started.elapsed();
    assert_eq!(status, StatusCode::OK, "the limit fails open");
    assert!(elapsed < Duration::from_millis(PAUSE_MS / 2), "{elapsed:?}");
}
