mod common;

use std::time::Duration;

use axum::Router;
use axum::http::header::{HOST, LOCATION};
use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::{IntoResponse, Redirect};
use axum::routing::{any, get};
use common::app::{MAX_BODY_BYTES, spawn_gateway, test_settings};
use common::client::{anonymous_client, authorization, client, send_raw};
use common::upstream::{Upstream, dead_upstream_url, spawn_upstream};

fn echo_routes() -> Router {
    let reply = async || (StatusCode::CREATED, [("x-upstream", "catalog")], "created");
    Router::new()
        .route("/api/v1/events", any(reply))
        .route("/api/v1/events/{*rest}", any(reply))
        .route("/health", get(async || "upstream internal health"))
}

async fn gateway_with_echo() -> (String, Upstream) {
    let upstream = spawn_upstream(echo_routes()).await;
    let gateway = spawn_gateway(&test_settings(&upstream.url)).await;
    (gateway, upstream)
}

fn trace_id_of(headers: &HeaderMap) -> String {
    let traceparent = headers["traceparent"].to_str().expect("an ASCII header");
    let trace_id = traceparent.split('-').nth(1);
    trace_id.expect("a W3C traceparent").to_owned()
}

#[tokio::test]
async fn forwards_the_request_and_returns_the_upstream_response() {
    let (gateway, upstream) = gateway_with_echo().await;

    let response = client()
        .post(format!("{gateway}/api/v1/events/7/sections?limit=5"))
        .header("x-custom", "kept")
        .body("payload")
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED);
    assert_eq!(response.headers()["x-upstream"], "catalog");
    assert_eq!(response.text().await.unwrap(), "created");

    let received = upstream.received();
    assert_eq!(received.len(), 1);
    assert_eq!(received[0].method, Method::POST);
    assert_eq!(received[0].uri, "/api/v1/events/7/sections?limit=5");
    assert_eq!(received[0].headers["x-custom"], "kept");
    assert_eq!(received[0].body, "payload");
}

#[tokio::test]
async fn forwards_the_collection_route_without_a_trailing_path() {
    let (gateway, upstream) = gateway_with_echo().await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED);
    assert_eq!(upstream.received()[0].uri, "/api/v1/events");
}

#[tokio::test]
async fn passes_upstream_errors_through_unchanged() {
    let routes = Router::new().route(
        "/api/v1/events/{*rest}",
        get(async || (StatusCode::NOT_FOUND, "catalog says no").into_response()),
    );
    let upstream = spawn_upstream(routes).await;
    let gateway = spawn_gateway(&test_settings(&upstream.url)).await;

    let response = client()
        .get(format!("{gateway}/api/v1/events/1"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(response.text().await.unwrap(), "catalog says no");
}

#[tokio::test]
async fn returns_redirects_to_the_client_with_the_public_host() {
    let routes = Router::new().route(
        "/api/v1/events/{*rest}",
        get(async |headers: HeaderMap| {
            let host = headers[HOST].to_str().unwrap().to_owned();
            Redirect::temporary(&format!("http://{host}/api/v1/events"))
        }),
    );
    let upstream = spawn_upstream(routes).await;
    let gateway = spawn_gateway(&test_settings(&upstream.url)).await;

    let response = client()
        .get(format!("{gateway}/api/v1/events/7/"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
    assert_eq!(
        response.headers()[LOCATION],
        format!("{gateway}/api/v1/events")
    );
    assert_eq!(upstream.received().len(), 1);
}

#[tokio::test]
async fn starts_a_new_trace_and_ignores_the_one_from_the_client() {
    let (gateway, upstream) = gateway_with_echo().await;
    let client_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736";

    client()
        .get(format!("{gateway}/api/v1/events"))
        .header(
            "traceparent",
            format!("00-{client_trace_id}-00f067aa0ba902b7-01"),
        )
        .header("tracestate", "vendor=client")
        .send()
        .await
        .unwrap();

    let received = upstream.received();
    let trace_id = trace_id_of(&received[0].headers);
    assert_eq!(trace_id.len(), 32);
    assert_ne!(trace_id, client_trace_id);
    assert!(!received[0].headers.contains_key("tracestate"));
}

#[tokio::test]
async fn gives_every_request_its_own_trace() {
    let (gateway, upstream) = gateway_with_echo().await;

    for _ in 0..2 {
        client()
            .get(format!("{gateway}/api/v1/events"))
            .send()
            .await
            .unwrap();
    }

    let received = upstream.received();
    assert_ne!(
        trace_id_of(&received[0].headers),
        trace_id_of(&received[1].headers)
    );
}

#[tokio::test]
async fn removes_hop_by_hop_headers() {
    let (gateway, upstream) = gateway_with_echo().await;

    client()
        .get(format!("{gateway}/api/v1/events"))
        .header("proxy-authorization", "Basic secret")
        .header("keep-alive", "timeout=5")
        .send()
        .await
        .unwrap();

    let headers = &upstream.received()[0].headers;
    assert!(!headers.contains_key("proxy-authorization"));
    assert!(!headers.contains_key("keep-alive"));
}

#[tokio::test]
async fn answers_unknown_routes_itself_in_the_common_error_format() {
    let (gateway, upstream) = gateway_with_echo().await;

    let response = client()
        .get(format!("{gateway}/api/v1/unknown"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert_eq!(
        response.text().await.unwrap(),
        r#"{"code":"not_found","message":"Not found","details":[]}"#
    );
    assert!(upstream.received().is_empty());
}

#[tokio::test]
async fn does_not_expose_internal_upstream_routes() {
    let (gateway, upstream) = gateway_with_echo().await;

    let response = client()
        .get(format!("{gateway}/api/v1/eventsfoo"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(upstream.received().is_empty());
}

#[tokio::test]
async fn refuses_paths_that_climb_out_of_the_public_route() {
    let (gateway, upstream) = gateway_with_echo().await;

    for path in [
        "/api/v1/events/../../../health",
        "/api/v1/events/%2e%2e/%2e%2e/%2e%2e/health",
        "/api/v1/events/%2E%2E/%2E%2E/%2E%2E/health",
    ] {
        let request = format!(
            "GET {path} HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\
             Authorization: {}\r\n\r\n",
            authorization()
        );

        let status = send_raw(&gateway, &request).await;

        assert_eq!(status, StatusCode::NOT_FOUND, "{path}");
    }
    assert!(upstream.received().is_empty());
}

#[tokio::test]
async fn health_and_ready_do_not_depend_on_upstreams() {
    let gateway = spawn_gateway(&test_settings(&dead_upstream_url().await)).await;

    for path in ["/health", "/ready"] {
        let response = anonymous_client()
            .get(format!("{gateway}{path}"))
            .send()
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK, "{path}");
        assert_eq!(response.text().await.unwrap(), r#"{"status":"ok"}"#);
    }
}

#[tokio::test]
async fn returns_503_when_the_upstream_is_down() {
    let gateway = spawn_gateway(&test_settings(&dead_upstream_url().await)).await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        response.text().await.unwrap(),
        r#"{"code":"service_unavailable","message":"Upstream is unavailable","details":[]}"#
    );
}

#[tokio::test]
async fn returns_504_when_the_upstream_is_too_slow() {
    let routes = Router::new().route(
        "/api/v1/events",
        get(async || tokio::time::sleep(Duration::from_secs(5)).await),
    );
    let upstream = spawn_upstream(routes).await;
    let mut settings = test_settings(&upstream.url);
    settings.client.request_timeout = Duration::from_millis(100);
    let gateway = spawn_gateway(&settings).await;

    let response = client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::GATEWAY_TIMEOUT);
    assert_eq!(
        response.text().await.unwrap(),
        r#"{"code":"gateway_timeout","message":"Upstream did not respond in time","details":[]}"#
    );
}

#[tokio::test]
async fn accepts_a_body_at_the_limit() {
    let (gateway, upstream) = gateway_with_echo().await;

    let response = client()
        .post(format!("{gateway}/api/v1/events"))
        .body("x".repeat(MAX_BODY_BYTES))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED);
    assert_eq!(upstream.received()[0].body.len(), MAX_BODY_BYTES);
}

#[tokio::test]
async fn rejects_a_body_over_the_limit() {
    let (gateway, upstream) = gateway_with_echo().await;

    let response = client()
        .post(format!("{gateway}/api/v1/events"))
        .body("x".repeat(MAX_BODY_BYTES + 1))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(
        response.text().await.unwrap(),
        r#"{"code":"payload_too_large","message":"Request body is too large","details":[]}"#
    );
    assert!(upstream.received().is_empty());
}

#[tokio::test]
async fn rejects_a_chunked_body_over_the_limit_without_a_content_length() {
    let (gateway, upstream) = gateway_with_echo().await;
    let size = MAX_BODY_BYTES + 1;
    let chunk = "x".repeat(size);
    let request = format!(
        "POST /api/v1/events HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\
         Authorization: {}\r\nTransfer-Encoding: chunked\r\n\r\n\
         {size:x}\r\n{chunk}\r\n0\r\n\r\n",
        authorization()
    );

    let status = send_raw(&gateway, &request).await;

    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    assert!(upstream.received().is_empty());
}

#[tokio::test]
async fn each_route_goes_to_its_own_upstream() {
    let catalog = spawn_upstream(echo_routes()).await;
    let seat_hold_routes = Router::new()
        .route("/api/v1/holds", any(async || StatusCode::CREATED))
        .route(
            "/api/v1/holds/{*rest}",
            any(async || StatusCode::NO_CONTENT),
        );
    let seat_hold = spawn_upstream(seat_hold_routes).await;
    let mut settings = test_settings(&catalog.url);
    settings.upstreams.seat_hold_url = seat_hold.url.parse().unwrap();
    let gateway = spawn_gateway(&settings).await;

    let created = client()
        .post(format!("{gateway}/api/v1/holds"))
        .body("{}")
        .send()
        .await
        .unwrap();
    let released = client()
        .delete(format!("{gateway}/api/v1/holds/7"))
        .send()
        .await
        .unwrap();
    client()
        .get(format!("{gateway}/api/v1/events"))
        .send()
        .await
        .unwrap();

    assert_eq!(created.status(), StatusCode::CREATED);
    assert_eq!(released.status(), StatusCode::NO_CONTENT);
    let holds = seat_hold.received();
    assert_eq!(holds.len(), 2);
    assert_eq!(holds[0].uri, "/api/v1/holds");
    assert_eq!(holds[1].method, Method::DELETE);
    assert_eq!(holds[1].uri, "/api/v1/holds/7");
    assert_eq!(catalog.received().len(), 1);
    assert_eq!(catalog.received()[0].uri, "/api/v1/events");
}

#[tokio::test]
async fn internal_routes_are_not_reachable_from_outside() {
    let (gateway, upstream) = gateway_with_echo().await;

    let response = client()
        .post(format!("{gateway}/internal/v1/holds/7/confirm"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(upstream.received().is_empty());
}
