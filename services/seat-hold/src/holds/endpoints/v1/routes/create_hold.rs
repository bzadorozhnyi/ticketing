use axum::Json;
use axum::extract::State;
use axum::extract::rejection::JsonRejection;
use axum::http::StatusCode;

use crate::core::db::DbPool;
use crate::core::errors::{ErrorOut, ServiceError};
use crate::core::user::UserId;
use crate::holds::dto::hold::NewHold;
use crate::holds::endpoints::v1::schemas::request::hold::HoldIn;
use crate::holds::endpoints::v1::schemas::response::hold::HoldOut;
use crate::holds::use_cases::hold_seats::HoldSeatsUseCase;

#[utoipa::path(
    post,
    path = "/api/v1/holds",
    tag = "holds",
    request_body = HoldIn,
    params(("x-user-id" = String, Header, description = "Set by the gateway from the token")),
    responses(
        (status = 201, body = HoldOut),
        (status = 401, body = ErrorOut),
        (status = 409, description = "A seat is taken; details list those seats", body = ErrorOut),
        (status = 422, body = ErrorOut),
        (status = 503, body = ErrorOut),
    )
)]
pub async fn create_hold<D: DbPool>(
    State(use_case): State<HoldSeatsUseCase<D>>,
    UserId(owner_id): UserId,
    body: Result<Json<HoldIn>, JsonRejection>,
) -> Result<(StatusCode, Json<HoldOut>), ServiceError> {
    let Json(input) = body.map_err(|error| ServiceError::Validation(error.body_text()))?;

    let hold = use_case
        .execute(NewHold {
            event_id: input.event_id,
            seat_ids: input.seat_ids,
            owner_id,
        })
        .await?;
    Ok((StatusCode::CREATED, Json(hold.into())))
}
