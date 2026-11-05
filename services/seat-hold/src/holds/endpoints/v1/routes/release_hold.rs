use axum::extract::rejection::PathRejection;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use uuid::Uuid;

use crate::core::db::DbPool;
use crate::core::errors::{ErrorOut, ServiceError};
use crate::core::user::UserId;
use crate::holds::use_cases::release_hold::ReleaseHoldUseCase;

#[utoipa::path(
    delete,
    path = "/api/v1/holds/{hold_id}",
    tag = "holds",
    params(
        ("hold_id" = Uuid, Path),
        ("x-user-id" = String, Header, description = "Set by the gateway from the token"),
    ),
    responses(
        (status = 204, description = "The caller holds nothing under this id"),
        (status = 401, body = ErrorOut),
        (status = 422, body = ErrorOut),
        (status = 503, body = ErrorOut),
    )
)]
pub async fn release_hold<D: DbPool>(
    State(use_case): State<ReleaseHoldUseCase<D>>,
    UserId(owner_id): UserId,
    hold_id: Result<Path<Uuid>, PathRejection>,
) -> Result<StatusCode, ServiceError> {
    let Path(hold_id) = hold_id.map_err(|error| ServiceError::Validation(error.body_text()))?;
    use_case.execute(hold_id, &owner_id).await?;
    Ok(StatusCode::NO_CONTENT)
}
