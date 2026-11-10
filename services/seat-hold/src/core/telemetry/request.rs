use std::time::Instant;

use axum::extract::{MatchedPath, Request};
use axum::http::{HeaderMap, Method};
use axum::middleware::Next;
use axum::response::Response;
use opentelemetry::context::FutureExt;
use opentelemetry::propagation::{Extractor, TextMapPropagator};
use opentelemetry::trace::{SpanKind, Status, TraceContextExt, Tracer};
use opentelemetry::{Context, KeyValue};
use opentelemetry_sdk::propagation::TraceContextPropagator;
use tracing::Instrument;

use crate::core::errors::ErrorCode;
use crate::core::telemetry::init::tracer;
use crate::core::telemetry::metrics::metrics;

/// Continues the gateway's trace from a valid `traceparent`; otherwise a new trace that
/// is not recorded. A forged header never reaches the logs: only the parsed id does.
/// Each request gets a server span, a sample of `http.server.request.duration` and a log
/// line. The context is attached to the task, so a repository's `db_span` finds its
/// parent without one more argument. It runs after routing, so the route is known.
pub async fn trace_request(request: Request, next: Next) -> Response {
    let method = method_name(request.method());
    let path = request.uri().path().to_owned();
    let route = request
        .extensions()
        .get::<MatchedPath>()
        .map(|route| route.as_str().to_owned());
    let parent = TraceContextPropagator::new().extract(&Headers(request.headers()));
    let span = tracer()
        .span_builder(method)
        .with_kind(SpanKind::Server)
        .start_with_context(tracer(), &parent);
    let context = parent.with_span(span);
    let trace_id = context.span().span_context().trace_id().to_string();
    let log_span = tracing::info_span!("request", trace_id);

    async move {
        let started = Instant::now();
        let response = next.run(request).await;
        let seconds = started.elapsed().as_secs_f64();
        finish(&Context::current(), method, route, &response, seconds);
        tracing::info!(
            method,
            path,
            status = response.status().as_u16(),
            duration_ms = seconds * 1000.0,
            "Request finished"
        );
        response
    }
    .with_context(context)
    .instrument(log_span)
    .await
}

fn finish(
    context: &Context,
    method: &'static str,
    route: Option<String>,
    response: &Response,
    seconds: f64,
) {
    let status = response.status();
    let span = context.span();
    if let Some(route) = &route {
        span.update_name(format!("{method} {route}"));
    }
    let mut attributes = vec![
        KeyValue::new("http.request.method", method),
        KeyValue::new("http.response.status_code", i64::from(status.as_u16())),
    ];
    if let Some(route) = route {
        attributes.push(KeyValue::new("http.route", route));
    }
    if let Some(code) = response.extensions().get::<ErrorCode>() {
        attributes.push(KeyValue::new("error.type", code.as_str()));
    }
    metrics().server_duration.record(seconds, &attributes);
    if status.is_server_error() {
        span.set_status(Status::error(status.to_string()));
    }
    span.set_attributes(attributes);
    span.end();
}

/// `http.request.method` as OpenTelemetry names it: anything unknown is `_OTHER`.
fn method_name(method: &Method) -> &'static str {
    match *method {
        Method::GET => "GET",
        Method::HEAD => "HEAD",
        Method::POST => "POST",
        Method::PUT => "PUT",
        Method::DELETE => "DELETE",
        Method::PATCH => "PATCH",
        Method::OPTIONS => "OPTIONS",
        _ => "_OTHER",
    }
}

struct Headers<'a>(&'a HeaderMap);

impl Extractor for Headers<'_> {
    fn get(&self, key: &str) -> Option<&str> {
        self.0.get(key).and_then(|value| value.to_str().ok())
    }

    fn keys(&self) -> Vec<&str> {
        self.0.keys().map(axum::http::HeaderName::as_str).collect()
    }
}
