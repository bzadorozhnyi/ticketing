use axum::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde::Serialize;
use thiserror::Error;
use utoipa::ToSchema;
use uuid::Uuid;

#[derive(Clone, Copy, Debug, Serialize, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    Conflict,
    InternalError,
    NotFound,
    ServiceUnavailable,
    Unauthorized,
    ValidationError,
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
            Self::SeatsTaken(_) | Self::HoldExpired | Self::HoldLimitReached(_) => {
                (StatusCode::CONFLICT, ErrorCode::Conflict)
            }
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

impl From<sqlx::Error> for ServiceError {
    fn from(error: sqlx::Error) -> Self {
        let is_outage = matches!(
            error,
            sqlx::Error::Io(_) | sqlx::Error::PoolTimedOut | sqlx::Error::PoolClosed
        );
        if is_outage {
            tracing::warn!(%error, "Database is unavailable");
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
        (status, Json(body)).into_response()
    }
}
