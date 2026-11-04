use std::time::Instant;

use axum::extract::Request;
use axum::middleware::Next;
use axum::response::Response;
use tracing::Instrument;

#[derive(Clone, Debug)]
pub struct TraceContext {
    pub trace_id: String,
}

impl TraceContext {
    #[must_use]
    pub fn generate() -> Self {
        Self {
            trace_id: format!("{:032x}", rand::random::<u128>()),
        }
    }

    /// Each call is a new hop of the same trace, so the parent id is new every time.
    #[must_use]
    pub fn traceparent(&self) -> String {
        format!("00-{}-{:016x}-01", self.trace_id, rand::random::<u64>())
    }
}

/// The gateway is the edge: a trace always starts here, whatever the client sent.
pub async fn trace_request(mut request: Request, next: Next) -> Response {
    let trace = TraceContext::generate();
    let span = tracing::info_span!("request", trace_id = %trace.trace_id);
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    request.extensions_mut().insert(trace);

    async move {
        let started = Instant::now();
        let response = next.run(request).await;
        tracing::info!(
            %method,
            path,
            status = response.status().as_u16(),
            duration_ms = started.elapsed().as_secs_f64() * 1000.0,
            "Request finished"
        );
        response
    }
    .instrument(span)
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn traceparent_has_the_w3c_shape() {
        let trace = TraceContext::generate();

        let traceparent = trace.traceparent();
        let parts: Vec<&str> = traceparent.split('-').collect();

        assert_eq!(parts.len(), 4);
        assert_eq!(parts[0], "00");
        assert_eq!(parts[1], trace.trace_id);
        assert_eq!(parts[1].len(), 32);
        assert_eq!(parts[2].len(), 16);
        assert_eq!(parts[3], "01");
    }

    #[test]
    fn every_hop_of_a_trace_has_its_own_parent_id() {
        let trace = TraceContext::generate();

        assert_ne!(trace.traceparent(), trace.traceparent());
    }
}
