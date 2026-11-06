use axum::Json;
use axum::extract::rejection::PathRejection;
use axum::extract::{Path, State};
use uuid::Uuid;

use crate::core::db::DbPool;
use crate::core::errors::{ErrorOut, ServiceError};
use crate::core::user::UserId;
use crate::holds::endpoints::internal::schemas::response::hold::ExtendedHoldOut;
use crate::holds::use_cases::extend_hold::ExtendHoldUseCase;

#[utoipa::path(
    post,
    path = "/internal/v1/holds/{hold_id}/extend",
    tag = "internal",
    params(
        ("hold_id" = Uuid, Path),
        ("x-user-id" = String, Header, description = "The owner of the hold"),
    ),
    responses(
        (status = 200, body = ExtendedHoldOut),
        (status = 401, body = ErrorOut),
        (status = 409, description = "The hold is gone: do not charge", body = ErrorOut),
        (status = 422, body = ErrorOut),
        (status = 503, body = ErrorOut),
    )
)]
pub async fn extend_hold<D: DbPool>(
    State(use_case): State<ExtendHoldUseCase<D>>,
    UserId(owner_id): UserId,
    hold_id: Result<Path<Uuid>, PathRejection>,
) -> Result<Json<ExtendedHoldOut>, ServiceError> {
    let Path(hold_id) = hold_id.map_err(|error| ServiceError::Validation(error.body_text()))?;
    let expires_at = use_case.execute(hold_id, &owner_id).await?;
    Ok(Json(ExtendedHoldOut {
        hold_id,
        expires_at,
    }))
}
