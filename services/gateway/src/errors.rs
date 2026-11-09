use axum::Json;
use axum::extract::rejection::BytesRejection;
use axum::http::StatusCode;
use std::time::Duration;

use axum::http::header::{HeaderValue, RETRY_AFTER, WWW_AUTHENTICATE};
use axum::response::{IntoResponse, Response};
use serde::Serialize;
use thiserror::Error;

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    BadGateway,
    BulkheadFull,
    CircuitOpen,
    GatewayTimeout,
    HttpError,
    NotFound,
    PayloadTooLarge,
    ServiceUnavailable,
    Unauthorized,
}

#[derive(Debug, Serialize)]
struct ErrorOut {
    code: ErrorCode,
    message: String,
    details: Vec<String>,
}

#[derive(Debug, Error)]
pub enum GatewayError {
    #[error("Authentication required")]
    Unauthorized,
    #[error("Not found")]
    NotFound,
    #[error("Request body is too large")]
    PayloadTooLarge,
    #[error("Failed to read the request body")]
    UnreadableBody,
    #[error("Upstream is unavailable")]
    UpstreamUnavailable,
    #[error("Upstream did not respond in time")]
    UpstreamTimeout,
    #[error("Upstream request failed")]
    UpstreamFailed,
    #[error("Upstream keeps failing and was not called")]
    CircuitOpen { retry_after: Duration },
    #[error("Too many requests to this upstream are in flight")]
    BulkheadFull,
}

impl GatewayError {
    fn status_and_code(&self) -> (StatusCode, ErrorCode) {
        match self {
            Self::Unauthorized => (StatusCode::UNAUTHORIZED, ErrorCode::Unauthorized),
            Self::NotFound => (StatusCode::NOT_FOUND, ErrorCode::NotFound),
            Self::PayloadTooLarge => (StatusCode::PAYLOAD_TOO_LARGE, ErrorCode::PayloadTooLarge),
            Self::UnreadableBody => (StatusCode::BAD_REQUEST, ErrorCode::HttpError),
            Self::UpstreamUnavailable => (
                StatusCode::SERVICE_UNAVAILABLE,
                ErrorCode::ServiceUnavailable,
            ),
            Self::UpstreamTimeout => (StatusCode::GATEWAY_TIMEOUT, ErrorCode::GatewayTimeout),
            Self::UpstreamFailed => (StatusCode::BAD_GATEWAY, ErrorCode::BadGateway),
            Self::CircuitOpen { .. } => (StatusCode::SERVICE_UNAVAILABLE, ErrorCode::CircuitOpen),
            Self::BulkheadFull => (StatusCode::SERVICE_UNAVAILABLE, ErrorCode::BulkheadFull),
        }
    }
}

impl From<BytesRejection> for GatewayError {
    fn from(rejection: BytesRejection) -> Self {
        if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE {
            Self::PayloadTooLarge
        } else {
            Self::UnreadableBody
        }
    }
}

impl From<reqwest::Error> for GatewayError {
    fn from(error: reqwest::Error) -> Self {
        let kind = if error.is_connect() {
            Self::UpstreamUnavailable
        } else if error.is_timeout() {
            Self::UpstreamTimeout
        } else {
            Self::UpstreamFailed
        };
        tracing::warn!(error = ?error.without_url(), "{kind}");
        kind
    }
}

impl IntoResponse for GatewayError {
    fn into_response(self) -> Response {
        let (status, code) = self.status_and_code();
        let body = ErrorOut {
            code,
            message: self.to_string(),
            details: Vec::new(),
        };
        let mut response = (status, Json(body)).into_response();
        if matches!(self, Self::Unauthorized) {
            let challenge = HeaderValue::from_static("Bearer");
            response.headers_mut().insert(WWW_AUTHENTICATE, challenge);
        }
        if let Self::CircuitOpen { retry_after } = self {
            let seconds = retry_after.as_secs() + u64::from(retry_after.subsec_nanos() > 0);
            response
                .headers_mut()
                .insert(RETRY_AFTER, HeaderValue::from(seconds.max(1)));
        }
        response
    }
}
