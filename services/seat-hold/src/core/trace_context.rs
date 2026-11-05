use std::time::Instant;

use axum::extract::Request;
use axum::middleware::Next;
use axum::response::Response;
use tracing::Instrument;

fn is_lowercase_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

fn is_valid_id(value: &str, length: usize) -> bool {
    is_lowercase_hex(value, length) && value.bytes().any(|byte| byte != b'0')
}

/// Returns the trace id of a valid W3C traceparent header, otherwise None.
/// Only version 00 forbids extra fields: later versions may add them.
#[must_use]
pub fn parse_trace_id(traceparent: &str) -> Option<&str> {
    let mut parts = traceparent.splitn(5, '-');
    let version = parts.next()?;
    let trace_id = parts.next()?;
    let parent_id = parts.next()?;
    let flags = parts.next()?;
    let has_extra = parts.next().is_some();

    let is_valid = is_lowercase_hex(version, 2)
        && version != "ff"
        && !(version == "00" && has_extra)
        && is_valid_id(trace_id, 32)
        && is_valid_id(parent_id, 16)
        && is_lowercase_hex(flags, 2);
    is_valid.then_some(trace_id)
}

fn trace_id_of(request: &Request) -> String {
    request
        .headers()
        .get("traceparent")
        .and_then(|value| value.to_str().ok())
        .and_then(parse_trace_id)
        .map_or_else(
            || format!("{:032x}", rand::random::<u128>()),
            ToOwned::to_owned,
        )
}

pub async fn trace_request(request: Request, next: Next) -> Response {
    let span = tracing::info_span!("request", trace_id = trace_id_of(&request));
    let method = request.method().clone();
    let path = request.uri().path().to_owned();

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

    const TRACE_ID: &str = "4bf92f3577b34da6a3ce929d0e0e4736";

    #[test]
    fn accepts_a_valid_header() {
        let header = format!("00-{TRACE_ID}-00f067aa0ba902b7-01");

        assert_eq!(parse_trace_id(&header), Some(TRACE_ID));
    }

    #[test]
    fn accepts_extra_fields_in_a_later_version() {
        let header = format!("01-{TRACE_ID}-00f067aa0ba902b7-01-extra");

        assert_eq!(parse_trace_id(&header), Some(TRACE_ID));
    }

    #[test]
    fn rejects_invalid_headers() {
        let zero_trace = "0".repeat(32);
        let upper_trace = TRACE_ID.to_uppercase();
        for header in [
            String::new(),
            "garbage".to_owned(),
            format!("ff-{TRACE_ID}-00f067aa0ba902b7-01"),
            format!("00-{TRACE_ID}-00f067aa0ba902b7-01-extra"),
            format!("00-{zero_trace}-00f067aa0ba902b7-01"),
            format!("00-{TRACE_ID}-0000000000000000-01"),
            format!("00-{upper_trace}-00f067aa0ba902b7-01"),
            format!("00-{TRACE_ID}-00f067aa0ba902b7-1"),
            format!("00-{TRACE_ID}\n-00f067aa0ba902b7-01"),
        ] {
            assert_eq!(parse_trace_id(&header), None, "{header}");
        }
    }
}
