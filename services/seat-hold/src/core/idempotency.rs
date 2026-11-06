use std::future::{Future, ready};

use axum::extract::FromRequestParts;
use axum::http::request::Parts;

use crate::core::errors::ServiceError;

const MAX_KEY_LENGTH: usize = 255;

/// The optional `Idempotency-Key` header: a repeat with the same key gets the first result.
pub struct IdempotencyKey(pub Option<String>);

impl<S: Send + Sync> FromRequestParts<S> for IdempotencyKey {
    type Rejection = ServiceError;

    fn from_request_parts(
        parts: &mut Parts,
        _state: &S,
    ) -> impl Future<Output = Result<Self, Self::Rejection>> + Send {
        ready(parse(parts))
    }
}

fn parse(parts: &Parts) -> Result<IdempotencyKey, ServiceError> {
    let Some(value) = parts.headers.get("idempotency-key") else {
        return Ok(IdempotencyKey(None));
    };
    match value.to_str() {
        Ok(key) if !key.is_empty() && key.len() <= MAX_KEY_LENGTH => {
            Ok(IdempotencyKey(Some(key.to_owned())))
        }
        _ => Err(ServiceError::Validation(format!(
            "Idempotency-Key must be 1 to {MAX_KEY_LENGTH} characters"
        ))),
    }
}
