use axum::Json;
use axum::extract::rejection::PathRejection;
use axum::extract::{Path, State};
use uuid::Uuid;

use crate::core::db::DbPool;
use crate::core::errors::{ErrorOut, ServiceError};
use crate::core::user::UserId;
use crate::holds::endpoints::v1::schemas::response::hold::HoldOut;
use crate::holds::use_cases::get_hold::GetHoldUseCase;

#[utoipa::path(
    get,
    path = "/internal/v1/holds/{hold_id}",
    tag = "internal",
    params(
        ("hold_id" = Uuid, Path),
        ("x-user-id" = String, Header, description = "The owner of the hold"),
    ),
    responses(
        (status = 200, body = HoldOut),
        (status = 401, body = ErrorOut),
        (status = 404, body = ErrorOut),
        (status = 422, body = ErrorOut),
        (status = 503, body = ErrorOut),
    )
)]
pub async fn get_hold<D: DbPool>(
    State(use_case): State<GetHoldUseCase<D>>,
    UserId(owner_id): UserId,
    hold_id: Result<Path<Uuid>, PathRejection>,
) -> Result<Json<HoldOut>, ServiceError> {
    let Path(hold_id) = hold_id.map_err(|error| ServiceError::Validation(error.body_text()))?;
    let hold = use_case.execute(hold_id, &owner_id).await?;
    Ok(Json(hold.into()))
}
