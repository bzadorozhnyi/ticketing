use axum::Extension;
use axum::body::{Body, Bytes};
use axum::extract::State;
use axum::extract::rejection::BytesRejection;
use axum::http::Uri;
use axum::http::header::{
    AUTHORIZATION, CONNECTION, HeaderMap, HeaderName, PROXY_AUTHENTICATE, PROXY_AUTHORIZATION, TE,
    TRAILER, TRANSFER_ENCODING, UPGRADE,
};
use axum::http::request::Parts;
use axum::response::Response;
use reqwest::Url;
use tokio::time::Instant;

use crate::app::AppState;
use crate::auth::AuthenticatedUser;
use crate::errors::GatewayError;
use crate::resilience::retry::{self, RequestKind};
use crate::telemetry::request::RequestContext;
use crate::upstream::Upstream;

const TRACEPARENT: HeaderName = HeaderName::from_static("traceparent");
const TRACESTATE: HeaderName = HeaderName::from_static("tracestate");
const X_USER_ID: HeaderName = HeaderName::from_static("x-user-id");
const HOP_BY_HOP: [HeaderName; 9] = [
    CONNECTION,
    HeaderName::from_static("keep-alive"),
    HeaderName::from_static("proxy-connection"),
    PROXY_AUTHENTICATE,
    PROXY_AUTHORIZATION,
    TE,
    TRAILER,
    TRANSFER_ENCODING,
    UPGRADE,
];

/// The request body is buffered, so `DefaultBodyLimit` applies to it and a retry can
/// send it again. The response is streamed, so its size does not cost the gateway memory.
/// All attempts together fit into the upstream's timeout. The bulkhead permit is held
/// until the response headers arrive, so a slow upstream cannot take every connection;
/// a body that streams slowly afterwards is not counted.
pub async fn forward(
    State(state): State<AppState>,
    Extension(upstream): Extension<Upstream>,
    Extension(RequestContext(context)): Extension<RequestContext>,
    user: Option<Extension<AuthenticatedUser>>,
    parts: Parts,
    body: Result<Bytes, BytesRejection>,
) -> Result<Response, GatewayError> {
    let url = upstream_url(&upstream.url, &parts.uri)?;
    let _in_flight = upstream
        .bulkhead
        .try_enter()
        .ok_or(GatewayError::BulkheadFull)?;
    let headers = request_headers(parts.headers, user.map(|user| user.0));
    let body = body?;
    let method = parts.method;
    let kind = RequestKind::of(&method, &headers);
    let deadline = Instant::now() + upstream.timeout;
    let response = retry::send(
        &context,
        &state.retry,
        &upstream.budget,
        &upstream.breaker,
        &kind,
        deadline,
        |remaining| {
            state
                .client
                .get()
                .request(method.clone(), url.clone())
                .headers(headers.clone())
                .body(body.clone())
                .timeout(remaining)
        },
    )
    .await?;
    Ok(into_response(response))
}

/// `Url` resolves `..` and `%2e%2e`, so `/api/v1/events/../../internal` would match the
/// public route here and reach a different path upstream. Such a request is refused.
fn upstream_url(base: &Url, uri: &Uri) -> Result<Url, GatewayError> {
    let mut url = base.clone();
    url.set_path(uri.path());
    url.set_query(uri.query());
    if url.path() == uri.path() {
        Ok(url)
    } else {
        Err(GatewayError::NotFound)
    }
}

/// A client's own `traceparent` and `tracestate` never pass: each attempt sends its own.
fn request_headers(mut headers: HeaderMap, user: Option<AuthenticatedUser>) -> HeaderMap {
    remove_hop_by_hop(&mut headers);
    headers.remove(AUTHORIZATION);
    headers.remove(TRACEPARENT);
    headers.remove(TRACESTATE);
    headers.remove(X_USER_ID);
    if let Some(AuthenticatedUser(user_id)) = user {
        headers.insert(X_USER_ID, user_id);
    }
    headers
}

fn into_response(upstream: reqwest::Response) -> Response {
    let status = upstream.status();
    let mut headers = upstream.headers().clone();
    remove_hop_by_hop(&mut headers);

    let mut response = Response::new(Body::from_stream(upstream.bytes_stream()));
    *response.status_mut() = status;
    *response.headers_mut() = headers;
    response
}

fn remove_hop_by_hop(headers: &mut HeaderMap) {
    for name in HOP_BY_HOP {
        headers.remove(name);
    }
}

#[cfg(test)]
mod tests {
    use axum::http::header::HeaderValue;

    use super::*;

    #[test]
    fn a_client_cannot_claim_a_user_on_a_public_route() {
        let mut headers = HeaderMap::new();
        headers.insert(X_USER_ID, HeaderValue::from_static("admin"));

        let forwarded = request_headers(headers, None);

        assert!(!forwarded.contains_key(X_USER_ID));
    }
}
