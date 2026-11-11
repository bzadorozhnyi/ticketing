use axum::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde::{Serialize, Serializer};
use sqlx::error::DatabaseError;
use thiserror::Error;
use utoipa::ToSchema;
use uuid::Uuid;

#[derive(Clone, Copy, Debug, ToSchema)]
#[schema(rename_all = "snake_case")]
pub enum ErrorCode {
    Conflict,
    HoldLimitReached,
    InternalError,
    NotFound,
    ServiceUnavailable,
    Unauthorized,
    ValidationError,
}

impl ErrorCode {
    /// The `code` of the response body; also the `error.type` of the request's metrics.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Conflict => "conflict",
            Self::HoldLimitReached => "hold_limit_reached",
            Self::InternalError => "internal_error",
            Self::NotFound => "not_found",
            Self::ServiceUnavailable => "service_unavailable",
            Self::Unauthorized => "unauthorized",
            Self::ValidationError => "validation_error",
        }
    }
}

impl Serialize for ErrorCode {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(self.as_str())
    }
}

#[derive(Debug, Serialize, ToSchema)]
pub struct ErrorOut {
    code: ErrorCode,
    message: String,
    details: Vec<String>,
}

#[derive(Debug, Error)]
pub enum ServiceError {
    #[error("Not found")]
    NotFound,
    #[error("Authentication required")]
    Unauthorized,
    #[error("{0}")]
    Validation(String),
    #[error("Some seats are taken")]
    SeatsTaken(Vec<Uuid>),
    #[error("At most {0} seats can be held at once for one event")]
    HoldLimitReached(i64),
    #[error("The hold has expired")]
    HoldExpired,
    #[error("Database is unavailable")]
    Unavailable,
    #[error("Internal server error")]
    Internal,
}

impl ServiceError {
    fn status_and_code(&self) -> (StatusCode, ErrorCode) {
        match self {
            Self::NotFound => (StatusCode::NOT_FOUND, ErrorCode::NotFound),
            Self::Unauthorized => (StatusCode::UNAUTHORIZED, ErrorCode::Unauthorized),
            Self::Validation(_) => (StatusCode::UNPROCESSABLE_ENTITY, ErrorCode::ValidationError),
            Self::SeatsTaken(_) | Self::HoldExpired => (StatusCode::CONFLICT, ErrorCode::Conflict),
            Self::HoldLimitReached(_) => (StatusCode::CONFLICT, ErrorCode::HoldLimitReached),
            Self::Unavailable => (
                StatusCode::SERVICE_UNAVAILABLE,
                ErrorCode::ServiceUnavailable,
            ),
            Self::Internal => (StatusCode::INTERNAL_SERVER_ERROR, ErrorCode::InternalError),
        }
    }

    fn details(&self) -> Vec<String> {
        match self {
            Self::SeatsTaken(seat_ids) => seat_ids.iter().map(ToString::to_string).collect(),
            Self::NotFound
            | Self::Unauthorized
            | Self::Validation(_)
            | Self::HoldExpired
            | Self::HoldLimitReached(_)
            | Self::Unavailable
            | Self::Internal => Vec::new(),
        }
    }
}

/// Postgres cancelled a statement: `lock_timeout` (55P03) or `statement_timeout` (57014).
const TIMED_OUT: [&str; 2] = ["55P03", "57014"];

impl From<sqlx::Error> for ServiceError {
    fn from(error: sqlx::Error) -> Self {
        let is_outage = matches!(
            error,
            sqlx::Error::Io(_) | sqlx::Error::PoolTimedOut | sqlx::Error::PoolClosed
        );
        let timed_out = error
            .as_database_error()
            .and_then(DatabaseError::code)
            .is_some_and(|code| TIMED_OUT.contains(&code.as_ref()));
        if is_outage {
            tracing::warn!(%error, "Database is unavailable");
            Self::Unavailable
        } else if timed_out {
            tracing::warn!(%error, "Database statement timed out");
            Self::Unavailable
        } else {
            tracing::error!(%error, "Database query failed");
            Self::Internal
        }
    }
}

impl IntoResponse for ServiceError {
    fn into_response(self) -> Response {
        let (status, code) = self.status_and_code();
        let body = ErrorOut {
            code,
            message: self.to_string(),
            details: self.details(),
        };
        let mut response = (status, Json(body)).into_response();
        response.extensions_mut().insert(code);
        response
    }
}
