use axum::Json;
use axum::extract::rejection::PathRejection;
use axum::extract::{Path, State};
use uuid::Uuid;

use crate::core::db::DbPool;
use crate::core::errors::{ErrorOut, ServiceError};
use crate::core::user::UserId;
use crate::holds::endpoints::internal::schemas::response::hold::ConfirmedHoldOut;
use crate::holds::use_cases::confirm_hold::ConfirmHoldUseCase;

#[utoipa::path(
    post,
    path = "/internal/v1/holds/{hold_id}/confirm",
    tag = "internal",
    params(
        ("hold_id" = Uuid, Path),
        ("x-user-id" = String, Header, description = "The owner of the hold"),
    ),
    responses(
        (status = 200, body = ConfirmedHoldOut),
        (status = 401, body = ErrorOut),
        (status = 409, description = "The hold is gone: refund the charge", body = ErrorOut),
        (status = 422, body = ErrorOut),
        (status = 503, body = ErrorOut),
    )
)]
pub async fn confirm_hold<D: DbPool>(
    State(use_case): State<ConfirmHoldUseCase<D>>,
    UserId(owner_id): UserId,
    hold_id: Result<Path<Uuid>, PathRejection>,
) -> Result<Json<ConfirmedHoldOut>, ServiceError> {
    let Path(hold_id) = hold_id.map_err(|error| ServiceError::Validation(error.body_text()))?;
    use_case.execute(hold_id, &owner_id).await?;
    Ok(Json(ConfirmedHoldOut { hold_id }))
}
