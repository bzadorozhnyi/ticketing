mod common;

use axum::http::StatusCode;
use common::db::TestPool;
use common::factories::hold_factory;
use common::service::{client, spawn_service};
use common::telemetry::spans;
use opentelemetry::trace::{SpanId, SpanKind, TraceId};
use opentelemetry::{Key, Value};
use opentelemetry_sdk::trace::SpanData;
use serde_json::json;
use uuid::Uuid;

const PARENT_ID: &str = "00f067aa0ba902b7";

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

/// A trace id that no other test in this binary uses, so its spans can be told apart.
fn fresh_traceparent() -> (TraceId, String) {
    let trace_id = format!("{:032x}", rand::random::<u128>());
    let header = format!("00-{trace_id}-{PARENT_ID}-01");
    (TraceId::from_hex(&trace_id).expect("a trace id"), header)
}

#[tokio::test]
async fn a_request_continues_the_callers_trace_down_to_each_statement() {
    let service = spawn_service(TestPool::new().await).await;
    let (trace_id, header) = fresh_traceparent();

    let response = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", "user-1")
        .header("traceparent", header)
        .json(&json!({"event_id": Uuid::now_v7(), "seat_ids": [Uuid::now_v7()]}))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CREATED);
    let spans = spans_of(trace_id);
    let server = spans
        .iter()
        .find(|s| s.span_kind == SpanKind::Server)
        .unwrap();
    assert_eq!(server.name, "POST /api/v1/holds");
    assert_eq!(server.parent_span_id, SpanId::from_hex(PARENT_ID).unwrap());
    let statements: Vec<&str> = spans
        .iter()
        .filter(|s| s.span_kind == SpanKind::Client)
        .map(|s| s.name.as_ref())
        .collect();
    assert!(statements.contains(&"SELECT holds"), "{statements:?}");
    assert!(
        statements.contains(&"SELECT pg_advisory_xact_lock"),
        "{statements:?}"
    );
    assert!(statements.contains(&"INSERT holds"), "{statements:?}");
    assert!(
        spans
            .iter()
            .filter(|s| s.span_kind == SpanKind::Client)
            .all(|s| s.parent_span_id == server.span_context.span_id()),
        "every statement is a child of the request"
    );
}

#[tokio::test]
async fn a_refusal_records_its_code_on_the_request() {
    let pool = TestPool::new().await;
    let service = spawn_service(pool.clone()).await;
    let held = hold_factory().pool(&pool).call().await;
    let (trace_id, header) = fresh_traceparent();

    let response = client()
        .post(format!("{service}/api/v1/holds"))
        .header("x-user-id", "user-1")
        .header("traceparent", header)
        .json(&json!({"event_id": held.event_id, "seat_ids": [held.seat_id]}))
        .send()
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::CONFLICT);
    let spans = spans_of(trace_id);
    let server = spans
        .iter()
        .find(|s| s.span_kind == SpanKind::Server)
        .unwrap();
    assert_eq!(
        attribute(server, "error.type"),
        Some(Value::from("conflict"))
    );
    assert_eq!(
        attribute(server, "http.response.status_code"),
        Some(Value::I64(409))
    );
}
