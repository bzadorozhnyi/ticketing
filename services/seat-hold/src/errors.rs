use axum::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde::Serialize;
use thiserror::Error;

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    NotFound,
}

#[derive(Debug, Serialize)]
struct ErrorOut {
    code: ErrorCode,
    message: String,
    details: Vec<String>,
}

#[derive(Debug, Error)]
pub enum ServiceError {
    #[error("Not found")]
    NotFound,
}

impl ServiceError {
    fn status_and_code(&self) -> (StatusCode, ErrorCode) {
        match self {
            Self::NotFound => (StatusCode::NOT_FOUND, ErrorCode::NotFound),
        }
    }
}

impl IntoResponse for ServiceError {
    fn into_response(self) -> Response {
        let (status, code) = self.status_and_code();
        let body = ErrorOut {
            code,
            message: self.to_string(),
            details: Vec::new(),
        };
        (status, Json(body)).into_response()
    }
}
