mod common;

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use axum::Router;
use axum::extract::State;
use axum::http::StatusCode;
use axum::routing::any;
use common::app::{gateway_for, spawn_gateway, test_settings};
use common::client::{client, get};
use common::upstream::{answering, dead_upstream_url, spawn_upstream};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

/// Answers 503 to the first `failures` calls and 200 after them.
fn flaky_routes(prefix: &str, failures: usize) -> Router {
    let calls = Arc::new(AtomicUsize::new(0));
    let reply = move || {
        let calls = calls.clone();
        async move {
            if calls.fetch_add(1, Ordering::SeqCst) < failures {
                StatusCode::SERVICE_UNAVAILABLE
            } else {
                StatusCode::OK
            }
        }
    };
    Router::new()
        .route(prefix, any(reply.clone()))
        .route(&format!("{prefix}/{{*rest}}"), any(reply))
}

type Switch = (Arc<AtomicBool>, Arc<AtomicUsize>);

/// Counts calls and answers 503 while the switch is down.
async fn switchable(State((down, calls)): State<Switch>) -> StatusCode {
    calls.fetch_add(1, Ordering::SeqCst);
    if down.load(Ordering::SeqCst) {
        StatusCode::SERVICE_UNAVAILABLE
    } else {
        StatusCode::OK
    }
}

#[tokio::test]
async fn a_get_is_repeated_after_a_503_and_the_client_sees_only_the_success() {
    let (gateway, upstream) = gateway_for(flaky_routes("/api/v1/events", 1), |_| {}).await;

    assert_eq!(get(&gateway).await, StatusCode::OK);

    let received = upstream.received();
    assert_eq!(received.len(), 2);
    assert_eq!(
        received[0].headers["x-user-id"],
        received[1].headers["x-user-id"]
    );
    assert_eq!(
        received[0].headers["traceparent"].to_str().unwrap()[..35],
        received[1].headers["traceparent"].to_str().unwrap()[..35]
    );
}

#[tokio::test]
async fn a_get_gives_up_after_the_last_attempt_with_the_upstream_answer() {
    let (gateway, upstream) = gateway_for(flaky_routes("/api/v1/events", 100), |_| {}).await;

    assert_eq!(get(&gateway).await, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(upstream.received().len(), 3);
}

#[tokio::test]
async fn a_post_is_never_repeated() {
    let (gateway, upstream) = gateway_for(flaky_routes("/api/v1/events", 1), |_| {}).await;

    let response = client()
        .post(format!("{gateway}/api/v1/events"))
        .body("{}")
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(upstream.received().len(), 1);
}

#[tokio::test]
async fn a_head_is_repeated_like_a_get() {
    let (gateway, upstream) = gateway_for(flaky_routes("/api/v1/events", 1), |_| {}).await;

    let response = client()
        .head(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(upstream.received().len(), 2);
}

#[tokio::test]
async fn a_500_is_not_repeated() {
    let routes = answering(StatusCode::INTERNAL_SERVER_ERROR, Duration::ZERO);
    let (gateway, upstream) = gateway_for(routes, |_| {}).await;

    assert_eq!(get(&gateway).await, StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(upstream.received().len(), 1);
}

#[tokio::test]
async fn a_timeout_is_not_repeated() {
    let routes = answering(StatusCode::OK, Duration::from_secs(3));
    let (gateway, upstream) = gateway_for(routes, |settings| {
        settings.client.request_timeout = Duration::from_millis(300);
    })
    .await;

    assert_eq!(get(&gateway).await, StatusCode::GATEWAY_TIMEOUT);
    assert_eq!(upstream.received().len(), 1);
}

#[tokio::test]
async fn a_retry_gets_only_the_rest_of_the_request_timeout() {
    let routes = answering(StatusCode::SERVICE_UNAVAILABLE, Duration::from_millis(600));
    let (gateway, upstream) = gateway_for(routes, |settings| {
        settings.client.request_timeout = Duration::from_secs(1);
        settings.retry.max_attempts = 10;
    })
    .await;

    let started = Instant::now();
    let status = get(&gateway).await;

    let elapsed = started.elapsed();
    assert!(elapsed < Duration::from_millis(1100), "{elapsed:?}");
    assert_eq!(status, StatusCode::GATEWAY_TIMEOUT);
    assert_eq!(upstream.received().len(), 2);
}

#[tokio::test]
async fn no_pause_runs_past_the_request_timeout() {
    let routes = answering(StatusCode::SERVICE_UNAVAILABLE, Duration::ZERO);
    let (gateway, _upstream) = gateway_for(routes, |settings| {
        settings.client.request_timeout = Duration::from_secs(1);
        settings.retry.base_delay = Duration::from_secs(10);
    })
    .await;

    for _ in 0..5 {
        let started = Instant::now();
        get(&gateway).await;
        let elapsed = started.elapsed();
        assert!(elapsed < Duration::from_millis(1100), "{elapsed:?}");
    }
}

#[tokio::test]
async fn repeats_wait_before_they_go() {
    let routes = answering(StatusCode::SERVICE_UNAVAILABLE, Duration::ZERO);
    let (gateway, _upstream) = gateway_for(routes, |settings| {
        settings.retry.base_delay = Duration::from_millis(100);
    })
    .await;

    let started = Instant::now();
    for _ in 0..10 {
        get(&gateway).await;
    }

    // The budget allows two repeats to each of the first five requests and one to the sixth:
    // random pauses up to 100 and 200 ms, 800 ms on average. Without pauses the ten take
    // about 20 ms. A simulation of a million runs gave a sum below 500 ms in 2 % of them
    // and below 200 ms in 0.0003 %.
    let elapsed = started.elapsed();
    assert!(elapsed > Duration::from_millis(200), "{elapsed:?}");
}

#[tokio::test]
async fn quiet_healthy_traffic_does_not_save_up_retries_for_an_outage() {
    let down = Arc::new(AtomicBool::new(false));
    let calls = Arc::new(AtomicUsize::new(0));
    let routes = Router::new()
        .route("/api/v1/events", any(switchable))
        .with_state((down.clone(), calls.clone()));
    let (gateway, _upstream) = gateway_for(routes, |settings| {
        settings.retry.budget_ratio = 0.5;
        settings.retry.budget_capacity = 2.0;
    })
    .await;
    for _ in 0..40 {
        get(&gateway).await;
    }

    down.store(true, Ordering::SeqCst);
    calls.store(0, Ordering::SeqCst);
    for _ in 0..4 {
        get(&gateway).await;
    }

    let retries = calls.load(Ordering::SeqCst) - 4;
    assert!(
        retries <= 4,
        "{retries} retries: 2 saved and 2 earned at most"
    );
}

#[tokio::test]
async fn the_budget_stops_retries_while_the_upstream_is_down() {
    let (gateway, upstream) = gateway_for(flaky_routes("/api/v1/events", 1000), |settings| {
        settings.retry.budget_ratio = 0.0;
        settings.retry.budget_capacity = 2.0;
    })
    .await;

    for _ in 0..20 {
        assert_eq!(get(&gateway).await, StatusCode::SERVICE_UNAVAILABLE);
    }

    assert_eq!(upstream.received().len(), 22);
}

#[tokio::test]
async fn the_budget_lets_retries_grow_only_with_the_traffic() {
    let (gateway, upstream) = gateway_for(flaky_routes("/api/v1/events", 1000), |settings| {
        settings.retry.budget_ratio = 0.25;
        settings.retry.budget_capacity = 1.0;
    })
    .await;

    for _ in 0..40 {
        get(&gateway).await;
    }

    let retries = upstream.received().len() - 40;
    assert!(
        (10..=11).contains(&retries),
        "{retries} retries for 40 requests"
    );
}

#[tokio::test]
async fn every_upstream_has_its_own_budget() {
    let catalog = spawn_upstream(flaky_routes("/api/v1/events", 1000)).await;
    let seat_hold = spawn_upstream(flaky_routes("/api/v1/holds", 1)).await;
    let mut settings = test_settings(&catalog.url);
    settings.upstreams.seat_hold_url = seat_hold.url.parse().unwrap();
    settings.retry.budget_ratio = 0.0;
    settings.retry.budget_capacity = 1.0;
    let gateway = spawn_gateway(&settings).await;

    get(&gateway).await;
    get(&gateway).await;
    let hold = client()
        .get(format!("{gateway}/api/v1/holds/7"))
        .send()
        .await
        .unwrap();

    assert_eq!(catalog.received().len(), 3);
    assert_eq!(hold.status(), StatusCode::OK);
    assert_eq!(seat_hold.received().len(), 2);
}

/// A raw upstream that closes the first `closes` connections without an answer, the way a
/// killed replica drops the requests it was serving, and answers 200 after that.
async fn spawn_closing_upstream(closes: usize) -> (String, Arc<AtomicUsize>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("a free local port");
    let url = format!("http://{}", listener.local_addr().expect("a bound address"));
    let connections = Arc::new(AtomicUsize::new(0));
    let counter = connections.clone();
    tokio::spawn(async move {
        while let Ok((mut stream, _)) = listener.accept().await {
            let seen = counter.fetch_add(1, Ordering::SeqCst);
            let mut request = [0_u8; 4096];
            let _ = stream.read(&mut request).await;
            if seen >= closes {
                let _ = stream
                    .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n")
                    .await;
            }
        }
    });
    (url, connections)
}

#[tokio::test]
async fn a_get_is_repeated_when_the_upstream_drops_the_connection() {
    let (url, connections) = spawn_closing_upstream(1).await;
    let gateway = spawn_gateway(&test_settings(&url)).await;

    assert_eq!(get(&gateway).await, StatusCode::OK);
    assert_eq!(connections.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn a_post_is_not_repeated_when_the_upstream_drops_the_connection() {
    let (url, connections) = spawn_closing_upstream(1).await;
    let gateway = spawn_gateway(&test_settings(&url)).await;

    let response = client()
        .post(format!("{gateway}/api/v1/events"))
        .body("{}")
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(connections.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn a_502_from_the_upstream_is_not_repeated() {
    let routes = answering(StatusCode::BAD_GATEWAY, Duration::ZERO);
    let (gateway, upstream) = gateway_for(routes, |_| {}).await;

    assert_eq!(get(&gateway).await, StatusCode::BAD_GATEWAY);
    assert_eq!(upstream.received().len(), 1);
}

#[tokio::test]
async fn a_refused_connection_is_reported_as_a_connect_error() {
    let url = dead_upstream_url().await;

    let error = reqwest::Client::new().get(url).send().await.unwrap_err();

    assert!(error.is_connect(), "{error:?}");
}
