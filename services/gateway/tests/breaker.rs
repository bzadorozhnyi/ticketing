mod common;

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use axum::Router;
use axum::extract::State;
use axum::http::StatusCode;
use axum::http::header::AUTHORIZATION;
use axum::routing::any;
use common::app::{gateway_for, spawn_gateway, test_settings};
use common::client::{authorization, client, get};
use common::upstream::{Upstream, answering, spawn_upstream};
use gateway::settings::{BreakerSettings, Settings};

/// 503 while `down` is set, then 200 after `delay`.
fn recovering(down: Arc<AtomicBool>, delay: Duration) -> Router {
    let reply = async move |State(down): State<Arc<AtomicBool>>| {
        if down.load(Ordering::SeqCst) {
            return StatusCode::SERVICE_UNAVAILABLE;
        }
        tokio::time::sleep(delay).await;
        StatusCode::OK
    };
    Router::new()
        .route("/api/v1/events", any(reply))
        .with_state(down)
}

/// A gateway whose catalog is down until the switch is flipped, with the breaker open.
async fn opened(delay: Duration) -> (String, Upstream, Arc<AtomicBool>) {
    let down = Arc::new(AtomicBool::new(true));
    let (gateway, upstream) = gateway_for(recovering(down.clone(), delay), |s| {
        breaker_on(s, SHORT_OPEN);
    })
    .await;
    for _ in 0..CALLS_TO_OPEN {
        get(&gateway).await;
    }
    (gateway, upstream, down)
}

/// Failed calls that open the breaker: the window and the minimum are both this size.
const CALLS_TO_OPEN: usize = 4;
/// Short enough to wait out in a test.
const SHORT_OPEN: Duration = Duration::from_millis(200);
/// Long enough not to end during a test.
const LONG_OPEN: Duration = Duration::from_secs(60);

/// Lets the open time pass, with a margin, so the next request is a probe.
async fn wait_out_open_time() {
    tokio::time::sleep(SHORT_OPEN + Duration::from_millis(50)).await;
}

/// Turns retries off, so every client request is exactly one call the breaker counts.
fn breaker_on(settings: &mut Settings, open: Duration) {
    settings.retry.max_attempts = 1;
    settings.breaker = BreakerSettings {
        window: CALLS_TO_OPEN,
        min_calls: CALLS_TO_OPEN,
        failure_percent: 50,
        open_duration: open,
    };
}

#[tokio::test]
async fn an_open_breaker_answers_at_once_without_calling_the_upstream() {
    let routes = answering(StatusCode::SERVICE_UNAVAILABLE, Duration::ZERO);
    let (gateway, upstream) = gateway_for(routes, |s| breaker_on(s, LONG_OPEN)).await;
    for _ in 0..CALLS_TO_OPEN {
        assert_eq!(get(&gateway).await, StatusCode::SERVICE_UNAVAILABLE);
    }

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let retry_after: u64 = response.headers()["retry-after"]
        .to_str()
        .unwrap()
        .parse()
        .unwrap();
    let open = LONG_OPEN.as_secs();
    assert!((open - 1..=open).contains(&retry_after), "{retry_after}");
    let body = response.text().await.unwrap();
    assert!(body.contains(r#""code":"circuit_open""#), "{body}");
    assert_eq!(upstream.received().len(), CALLS_TO_OPEN);
}

#[tokio::test]
async fn timeouts_open_the_breaker() {
    let routes = answering(StatusCode::OK, Duration::from_secs(5));
    let (gateway, upstream) = gateway_for(routes, |s| {
        breaker_on(s, LONG_OPEN);
        s.upstreams.catalog_timeout = Some(Duration::from_millis(100));
    })
    .await;
    for _ in 0..CALLS_TO_OPEN {
        assert_eq!(get(&gateway).await, StatusCode::GATEWAY_TIMEOUT);
    }

    let started = Instant::now();
    assert_eq!(get(&gateway).await, StatusCode::SERVICE_UNAVAILABLE);

    assert!(started.elapsed() < Duration::from_millis(50));
    assert_eq!(upstream.received().len(), CALLS_TO_OPEN);
}

#[tokio::test]
async fn answers_that_show_a_working_upstream_do_not_open_it() {
    for status in [StatusCode::NOT_FOUND, StatusCode::INTERNAL_SERVER_ERROR] {
        let routes = answering(status, Duration::ZERO);
        let (gateway, upstream) = gateway_for(routes, |s| breaker_on(s, LONG_OPEN)).await;

        for _ in 0..10 {
            assert_eq!(get(&gateway).await, status);
        }

        assert_eq!(upstream.received().len(), 10, "{status}");
    }
}

#[tokio::test]
async fn after_the_open_time_one_probe_goes_and_its_success_closes_the_breaker() {
    let (gateway, upstream, down) = opened(Duration::from_millis(300)).await;
    down.store(false, Ordering::SeqCst);
    wait_out_open_time().await;

    let requests: Vec<_> = (0..5)
        .map(|_| {
            let gateway = gateway.clone();
            tokio::spawn(async move { get(&gateway).await })
        })
        .collect();
    let mut statuses = Vec::new();
    for request in requests {
        statuses.push(request.await.unwrap());
    }

    let passed = statuses.iter().filter(|s| **s == StatusCode::OK).count();
    assert_eq!(passed, 1, "{statuses:?}");
    assert_eq!(
        upstream.received().len(),
        CALLS_TO_OPEN + 1,
        "one probe after the failures"
    );
    assert_eq!(get(&gateway).await, StatusCode::OK, "the probe closed it");
}

#[tokio::test]
async fn a_failed_probe_keeps_it_open() {
    let (gateway, upstream, _down) = opened(Duration::ZERO).await;
    wait_out_open_time().await;

    get(&gateway).await;
    get(&gateway).await;

    assert_eq!(
        upstream.received().len(),
        5,
        "the probe and nothing after it"
    );
}

#[tokio::test]
async fn a_probe_abandoned_by_its_client_lets_the_next_request_probe() {
    let (gateway, upstream, down) = opened(Duration::from_millis(500)).await;
    down.store(false, Ordering::SeqCst);
    wait_out_open_time().await;

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
    assert_eq!(
        upstream.received().len(),
        CALLS_TO_OPEN + 2,
        "two probes after the failures"
    );
}

#[tokio::test]
async fn a_breaker_opening_during_retries_returns_the_last_upstream_answer() {
    let routes = answering(StatusCode::SERVICE_UNAVAILABLE, Duration::ZERO);
    let (gateway, upstream) = gateway_for(routes, |s| {
        breaker_on(s, LONG_OPEN);
        s.retry.max_attempts = 3;
        s.breaker.window = 2;
        s.breaker.min_calls = 2;
    })
    .await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert!(!response.headers().contains_key("retry-after"));
    assert_eq!(upstream.received().len(), 2);
}

#[tokio::test]
async fn every_upstream_has_its_own_breaker() {
    let catalog = spawn_upstream(answering(StatusCode::SERVICE_UNAVAILABLE, Duration::ZERO)).await;
    let holds = Router::new().route("/api/v1/holds/{*rest}", any(async || StatusCode::OK));
    let seat_hold = spawn_upstream(holds).await;
    let mut settings = test_settings(&catalog.url);
    breaker_on(&mut settings, LONG_OPEN);
    settings.upstreams.seat_hold_url = seat_hold.url.parse().unwrap();
    let gateway = spawn_gateway(&settings).await;
    for _ in 0..=CALLS_TO_OPEN {
        get(&gateway).await;
    }

    let hold = client()
        .get(format!("{gateway}/api/v1/holds/7"))
        .send()
        .await
        .unwrap();

    assert_eq!(catalog.received().len(), CALLS_TO_OPEN);
    assert_eq!(hold.status(), StatusCode::OK);
}

#[tokio::test]
async fn each_upstream_can_have_its_own_timeout() {
    let routes = answering(StatusCode::OK, Duration::from_secs(1));
    let (gateway, _upstream) = gateway_for(routes, |s| {
        s.upstreams.catalog_timeout = Some(Duration::from_millis(150));
    })
    .await;

    let started = Instant::now();
    assert_eq!(get(&gateway).await, StatusCode::GATEWAY_TIMEOUT);

    assert!(started.elapsed() < Duration::from_millis(400));
}
