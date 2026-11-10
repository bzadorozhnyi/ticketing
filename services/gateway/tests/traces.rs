mod common;

use std::time::Duration;

use axum::http::StatusCode;
use common::app::gateway_for;
use common::client::client;
use common::telemetry::spans;
use common::upstream::answering;
use opentelemetry::trace::{SpanId, SpanKind, Status, TraceId};
use opentelemetry::{Key, Value};
use opentelemetry_sdk::trace::SpanData;

fn spans_of(trace_id: TraceId) -> Vec<SpanData> {
    let all = spans().get_finished_spans().expect("finished spans");
    all.into_iter()
        .filter(|span| span.span_context.trace_id() == trace_id)
        .collect()
}

fn attribute(span: &SpanData, key: &'static str) -> Option<Value> {
    span.attributes
        .iter()
        .find(|attribute| attribute.key == Key::from_static_str(key))
        .map(|attribute| attribute.value.clone())
}

/// `00-<trace id>-<parent id>-<flags>` as the upstream received it.
fn traceparent_of(headers: &axum::http::HeaderMap) -> (TraceId, SpanId) {
    let traceparent = headers
        .get("traceparent")
        .and_then(|value| value.to_str().ok())
        .expect("a traceparent");
    let mut parts = traceparent.split('-').skip(1);
    let trace_id = parts.next().and_then(|id| TraceId::from_hex(id).ok());
    let parent_id = parts.next().and_then(|id| SpanId::from_hex(id).ok());
    (
        trace_id.expect("a trace id"),
        parent_id.expect("a parent id"),
    )
}

#[tokio::test]
async fn a_request_and_its_call_to_the_upstream_are_one_trace() {
    let (gateway, upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |_| {}).await;

    client()
        .get(format!("{gateway}/api/v1/events/42"))
        .send()
        .await
        .unwrap();

    let (trace_id, parent_id) = traceparent_of(&upstream.received()[0].headers);
    let spans = spans_of(trace_id);
    let server = spans
        .iter()
        .find(|s| s.span_kind == SpanKind::Server)
        .unwrap();
    let call = spans
        .iter()
        .find(|s| s.span_kind == SpanKind::Client)
        .unwrap();
    assert_eq!(server.name, "GET /api/v1/events/{*rest}");
    assert_eq!(
        server.parent_span_id,
        SpanId::INVALID,
        "a new trace starts here"
    );
    assert_eq!(call.name, "GET");
    assert_eq!(
        attribute(call, "server.address"),
        Some(Value::from("catalog"))
    );
    assert_eq!(call.parent_span_id, server.span_context.span_id());
    assert_eq!(
        call.span_context.span_id(),
        parent_id,
        "the upstream's parent is the call"
    );
    assert_eq!(
        attribute(call, "http.response.status_code"),
        Some(Value::I64(200))
    );
}

#[tokio::test]
async fn a_retry_is_a_second_attempt_in_the_same_trace() {
    let (gateway, upstream) = gateway_for(
        answering(StatusCode::SERVICE_UNAVAILABLE, Duration::ZERO),
        |s| {
            s.retry.max_attempts = 2;
        },
    )
    .await;

    client()
        .get(format!("{gateway}/api/v1/events/1"))
        .send()
        .await
        .unwrap();

    let received = upstream.received();
    let (trace_id, first) = traceparent_of(&received[0].headers);
    let (_, second) = traceparent_of(&received[1].headers);
    let spans = spans_of(trace_id);
    let server = spans
        .iter()
        .find(|s| s.span_kind == SpanKind::Server)
        .unwrap();
    let calls: Vec<&SpanData> = spans
        .iter()
        .filter(|s| s.span_kind == SpanKind::Client)
        .collect();
    assert_eq!(calls.len(), 2);
    assert!(
        calls
            .iter()
            .all(|c| c.parent_span_id == server.span_context.span_id())
    );
    assert_ne!(first, second, "each attempt is its own parent");
    let resent = calls
        .iter()
        .find(|c| c.span_context.span_id() == second)
        .unwrap();
    assert_eq!(
        attribute(resent, "http.request.resend_count"),
        Some(Value::I64(1))
    );
}

#[tokio::test]
async fn a_refusal_records_its_reason_on_the_request() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |s| {
        s.limits.max_in_flight = 0;
    })
    .await;

    let response = client()
        .get(format!("{gateway}/api/v1/orders/7"))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let all = spans().get_finished_spans().unwrap();
    let refused = all
        .iter()
        .find(|s| attribute(s, "error.type") == Some(Value::from("overloaded")))
        .expect("a span of the refused request");
    assert_eq!(refused.name, "GET /api/v1/orders/{*rest}");
    assert_eq!(
        attribute(refused, "http.response.status_code"),
        Some(Value::I64(503))
    );
    assert!(matches!(refused.status, Status::Error { .. }));
}

#[tokio::test]
async fn a_path_that_matched_no_route_is_named_by_its_method() {
    let (gateway, _upstream) = gateway_for(answering(StatusCode::OK, Duration::ZERO), |_| {}).await;

    client()
        .get(format!("{gateway}/unknown/route"))
        .send()
        .await
        .unwrap();

    let all = spans().get_finished_spans().unwrap();
    let unmatched = all
        .iter()
        .find(|s| attribute(s, "error.type") == Some(Value::from("not_found")))
        .expect("a span of the unmatched request");
    assert_eq!(unmatched.name, "GET");
    assert_eq!(attribute(unmatched, "http.route"), None);
}
