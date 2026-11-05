use std::future::{Future, ready};

use axum::extract::FromRequestParts;
use axum::http::request::Parts;

use crate::core::errors::ServiceError;

/// The caller, as the gateway reports it. Services trust this header because they
/// are reachable only through the gateway.
pub struct UserId(pub String);

impl<S: Send + Sync> FromRequestParts<S> for UserId {
    type Rejection = ServiceError;

    fn from_request_parts(
        parts: &mut Parts,
        _state: &S,
    ) -> impl Future<Output = Result<Self, Self::Rejection>> + Send {
        let user = parts
            .headers
            .get("x-user-id")
            .and_then(|value| value.to_str().ok())
            .filter(|value| !value.is_empty())
            .map(|value| Self(value.to_owned()))
            .ok_or(ServiceError::Unauthorized);
        ready(user)
    }
}
