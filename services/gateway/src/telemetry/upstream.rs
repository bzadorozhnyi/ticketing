use std::time::Instant;

use opentelemetry::trace::{SpanKind, TraceContextExt, Tracer};
use opentelemetry::{Context, KeyValue};
use reqwest::header::HeaderName;
use reqwest::{Method, RequestBuilder, Response};

use crate::telemetry::init::tracer;
use crate::telemetry::method::method_name;
use crate::telemetry::metrics::metrics;

const TRACEPARENT: HeaderName = HeaderName::from_static("traceparent");

/// One attempt to call an upstream: a client span, a child of the request's span, and a
/// sample of `http.client.request.duration`. A retry is a second attempt next to the first.
pub struct UpstreamAttempt {
    context: Context,
    attributes: Vec<KeyValue>,
    started: Instant,
}

impl UpstreamAttempt {
    #[must_use]
    pub fn start(parent: &Context, upstream: &'static str, method: &Method, attempt: u32) -> Self {
        let method = method_name(method);
        let span = tracer()
            .span_builder(method)
            .with_kind(SpanKind::Client)
            .start_with_context(tracer(), parent);
        let mut attributes = vec![
            KeyValue::new("http.request.method", method),
            KeyValue::new("server.address", upstream),
        ];
        if attempt > 1 {
            attributes.push(KeyValue::new(
                "http.request.resend_count",
                i64::from(attempt - 1),
            ));
        }
        Self {
            context: parent.with_span(span),
            attributes,
            started: Instant::now(),
        }
    }

    /// Adds this attempt's W3C `traceparent`, so the upstream's spans become its children.
    pub fn propagate(&self, request: RequestBuilder) -> RequestBuilder {
        let span = self.context.span();
        let attempt = span.span_context();
        if !attempt.is_valid() {
            return request;
        }
        let traceparent = format!(
            "00-{:032x}-{:016x}-{:02x}",
            attempt.trace_id(),
            attempt.span_id(),
            attempt.trace_flags().to_u8()
        );
        request.header(TRACEPARENT, traceparent)
    }

    pub fn finish(mut self, result: &Result<Response, reqwest::Error>) {
        match result {
            Ok(response) => self.attributes.push(KeyValue::new(
                "http.response.status_code",
                i64::from(response.status().as_u16()),
            )),
            Err(error) => {
                let kind = if error.is_timeout() {
                    "timeout"
                } else {
                    "connection"
                };
                self.attributes.push(KeyValue::new("error.type", kind));
            }
        }
        let seconds = self.started.elapsed().as_secs_f64();
        metrics().client_duration.record(seconds, &self.attributes);
        let span = self.context.span();
        span.set_attributes(self.attributes);
        span.end();
    }
}
