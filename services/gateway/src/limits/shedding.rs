use axum::extract::{Request, State};
use axum::middleware::Next;
use axum::response::Response;

use crate::errors::GatewayError;
use crate::limits::concurrency::ConcurrencyLimit;

/// Refuses a request at once while the gateway has `LIMITS__MAX_IN_FLIGHT` requests in
/// work, before any other check: the refusal must be the cheapest answer it has. A place
/// is held until the response headers are sent. Health routes are not limited: a busy
/// gateway is alive, and a replica failing its probes would only move its load to others.
pub async fn shed(
    State(limit): State<ConcurrencyLimit>,
    request: Request,
    next: Next,
) -> Result<Response, GatewayError> {
    let _in_flight = limit.try_enter().ok_or(GatewayError::Overloaded)?;
    Ok(next.run(request).await)
}
