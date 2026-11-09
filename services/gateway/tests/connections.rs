mod common;

use std::time::{Duration, Instant};

use axum::http::StatusCode;
use common::app::{gateway_for, test_settings};
use common::client::client;
use common::upstream::{answering, spawn_upstream};
use gateway::app::build_app;
use gateway::server::serve::serve;
use gateway::settings::Settings;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;

const HEALTH: &[u8] = b"GET /health HTTP/1.1\r\nHost: gateway\r\n\r\n";
const HEADER_TIMEOUT: Duration = Duration::from_millis(200);

async fn connect(gateway: &str) -> TcpStream {
    TcpStream::connect(gateway.trim_start_matches("http://"))
        .await
        .expect("a running gateway")
}

/// What the gateway sends within `wait`: `None` if nothing, an empty vector if it closed.
async fn read_within(stream: &mut TcpStream, wait: Duration) -> Option<Vec<u8>> {
    let mut buffer = vec![0; 1024];
    match tokio::time::timeout(wait, stream.read(&mut buffer)).await {
        Err(_) => None,
        Ok(Ok(read)) => Some(buffer.into_iter().take(read).collect()),
        Ok(Err(_)) => Some(Vec::new()),
    }
}

async fn answers_health(stream: &mut TcpStream) -> bool {
    stream.write_all(HEALTH).await.expect("a writable socket");
    read_within(stream, Duration::from_secs(1))
        .await
        .is_some_and(|answer| answer.starts_with(b"HTTP/1.1 200"))
}

/// Waits until `stream` is closed by the gateway and returns how long that took.
async fn closed_after(stream: &mut TcpStream, limit: Duration) -> Option<Duration> {
    let started = Instant::now();
    match read_within(stream, limit).await {
        Some(answer) if answer.is_empty() => Some(started.elapsed()),
        _ => None,
    }
}

fn limited(settings: &mut Settings) {
    settings.limits.max_connections = 1;
    settings.limits.header_timeout = HEADER_TIMEOUT;
}

#[tokio::test]
async fn a_connection_over_the_limit_is_closed_at_once() {
    let (gateway, _upstream) =
        gateway_for(answering(StatusCode::OK, Duration::ZERO), limited).await;
    let mut first = connect(&gateway).await;
    assert!(answers_health(&mut first).await);

    let mut second = connect(&gateway).await;
    second.write_all(HEALTH).await.ok();

    let closed = closed_after(&mut second, HEADER_TIMEOUT / 2).await;
    assert!(closed.is_some(), "the second connection was served");
    assert!(answers_health(&mut first).await, "the first one stays");
}

#[tokio::test]
async fn a_place_frees_up_when_a_connection_closes() {
    let (gateway, _upstream) =
        gateway_for(answering(StatusCode::OK, Duration::ZERO), limited).await;
    let mut first = connect(&gateway).await;
    assert!(answers_health(&mut first).await);
    drop(first);
    tokio::time::sleep(Duration::from_millis(50)).await;

    let mut second = connect(&gateway).await;

    assert!(answers_health(&mut second).await);
}

#[tokio::test]
async fn a_client_that_sends_half_the_headers_is_closed() {
    let (gateway, _upstream) =
        gateway_for(answering(StatusCode::OK, Duration::ZERO), limited).await;
    let mut slow = connect(&gateway).await;
    slow.write_all(b"GET /hea").await.unwrap();

    let closed = closed_after(&mut slow, HEADER_TIMEOUT * 5).await;

    let closed = closed.expect("still open");
    assert!(closed >= HEADER_TIMEOUT / 2, "{closed:?}");
}

#[tokio::test]
async fn an_idle_keep_alive_connection_is_closed() {
    let (gateway, _upstream) =
        gateway_for(answering(StatusCode::OK, Duration::ZERO), limited).await;
    let mut idle = connect(&gateway).await;
    assert!(answers_health(&mut idle).await);

    let closed = closed_after(&mut idle, HEADER_TIMEOUT * 5).await;

    assert!(closed.is_some(), "still open");
}

#[tokio::test]
async fn a_slow_upstream_is_not_cut_by_the_header_timeout() {
    let (gateway, _upstream) =
        gateway_for(answering(StatusCode::OK, HEADER_TIMEOUT * 3), limited).await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn shutdown_lets_a_request_in_flight_finish_and_stops_accepting() {
    let upstream = spawn_upstream(answering(StatusCode::OK, Duration::from_millis(300))).await;
    let settings = test_settings(&upstream.url);
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
    let gateway = format!("http://{}", listener.local_addr().unwrap());
    let (stop, stopped) = oneshot::channel::<()>();
    let server = tokio::spawn(async move {
        let app = build_app(&settings).unwrap();
        serve(listener, app, &settings.limits, async {
            stopped.await.ok();
        })
        .await;
    });
    let in_flight = {
        let gateway = gateway.clone();
        tokio::spawn(async move {
            client()
                .get(format!("{gateway}/api/v1/events"))
                .send()
                .await
        })
    };
    tokio::time::sleep(Duration::from_millis(100)).await;

    stop.send(()).unwrap();
    tokio::time::sleep(Duration::from_millis(50)).await;

    assert!(
        TcpStream::connect(gateway.trim_start_matches("http://"))
            .await
            .is_err(),
        "a new connection was accepted while draining"
    );
    assert!(!server.is_finished(), "stopped before the request finished");
    assert_eq!(in_flight.await.unwrap().unwrap().status(), StatusCode::OK);
    tokio::time::timeout(Duration::from_secs(1), server)
        .await
        .expect("the server stopped")
        .unwrap();
    assert!(
        TcpStream::connect(gateway.trim_start_matches("http://"))
            .await
            .is_err()
    );
}
