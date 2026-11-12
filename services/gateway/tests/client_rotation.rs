mod common;

use std::collections::HashSet;
use std::net::SocketAddr;
use std::time::Duration;

use axum::Router;
use axum::extract::ConnectInfo;
use axum::http::StatusCode;
use axum::routing::get;
use common::app::{spawn_gateway, test_settings};
use common::client::client;
use common::upstream::{answering, spawn_upstream};
use tokio::net::TcpListener;
use tokio::time::{Instant, sleep};

/// An upstream that answers with the port of the connection the request came on.
async fn spawn_port_echo() -> String {
    let app = Router::new().route(
        "/api/v1/events",
        get(async |ConnectInfo(peer): ConnectInfo<SocketAddr>| peer.port().to_string()),
    );
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .await
        .expect("a free port");
    let addr = listener.local_addr().expect("a bound address");
    tokio::spawn(async move {
        axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await
    });
    format!("http://{addr}")
}

/// The connections the gateway used for requests sent one after another for `duration`.
async fn connections_used(max_age: Duration, duration: Duration) -> usize {
    let mut settings = test_settings(&spawn_port_echo().await);
    settings.client.pool_max_age = max_age;
    let gateway = spawn_gateway(&settings).await;
    let client = client();
    let mut ports = HashSet::new();
    let end = Instant::now() + duration;
    while Instant::now() < end {
        let response = client
            .get(format!("{gateway}/api/v1/events"))
            .send()
            .await
            .expect("a running gateway");
        ports.insert(response.text().await.expect("a readable body"));
        sleep(Duration::from_millis(20)).await;
    }
    ports.len()
}

#[tokio::test]
async fn kept_alive_connection_is_reused_without_a_max_age() {
    assert_eq!(
        connections_used(Duration::ZERO, Duration::from_millis(600)).await,
        1
    );
}

#[tokio::test]
async fn a_new_connection_is_opened_after_each_max_age() {
    let used = connections_used(Duration::from_millis(150), Duration::from_millis(600)).await;

    assert!((3..=5).contains(&used), "{used} connections in 600 ms");
}

#[tokio::test]
async fn a_call_in_flight_when_the_client_is_replaced_still_completes() {
    let upstream = spawn_upstream(answering(StatusCode::OK, Duration::from_millis(400))).await;
    let mut settings = test_settings(&upstream.url);
    settings.client.pool_max_age = Duration::from_millis(50);
    let gateway = spawn_gateway(&settings).await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .expect("a running gateway");

    assert_eq!(response.status(), StatusCode::OK);
}
