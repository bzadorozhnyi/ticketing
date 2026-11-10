use std::time::Instant;

use axum::extract::{MatchedPath, Request};
use axum::middleware::Next;
use axum::response::Response;
use opentelemetry::trace::{SpanKind, Status, TraceContextExt, Tracer};
use opentelemetry::{Context, KeyValue};
use tracing::Instrument;

use crate::errors::ErrorCode;
use crate::telemetry::init::tracer;
use crate::telemetry::method::method_name;
use crate::telemetry::metrics::metrics;

/// The request's span, for the calls it makes. Passed explicitly rather than attached to
/// the task: attaching costs on every poll, and a proxied request is polled many times.
#[derive(Clone)]
pub struct RequestContext(pub Context);

/// The gateway is the edge: a trace always starts here, whatever the client sent. Each
/// request gets a server span, a sample of `http.server.request.duration` and a log line.
/// It runs right after routing, so the route template is known; a path that matched no
/// route has none and is named by its method.
pub async fn trace_request(mut request: Request, next: Next) -> Response {
    let method = method_name(request.method());
    let path = request.uri().path().to_owned();
    let route = request
        .extensions()
        .get::<MatchedPath>()
        .map(|route| route.as_str().to_owned());
    let span = tracer()
        .span_builder(method)
        .with_kind(SpanKind::Server)
        .start_with_context(tracer(), &Context::new());
    let context = Context::new().with_span(span);
    let trace_id = context.span().span_context().trace_id().to_string();
    let log_span = tracing::info_span!("request", trace_id);
    request
        .extensions_mut()
        .insert(RequestContext(context.clone()));

    async move {
        let started = Instant::now();
        let response = next.run(request).await;
        let seconds = started.elapsed().as_secs_f64();
        finish(&context, method, route, &response, seconds);
        tracing::info!(
            method,
            path,
            status = response.status().as_u16(),
            duration_ms = seconds * 1000.0,
            "Request finished"
        );
        response
    }
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
