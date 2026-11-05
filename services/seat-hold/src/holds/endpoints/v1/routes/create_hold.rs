use axum::Json;
use axum::extract::State;
use axum::extract::rejection::JsonRejection;
use axum::http::{HeaderMap, StatusCode};

use crate::app::AppState;
use crate::core::db::DbPool;
use crate::core::errors::{ErrorOut, ServiceError};
use crate::holds::dto::hold::NewHold;
use crate::holds::endpoints::v1::schemas::request::hold::HoldIn;
use crate::holds::endpoints::v1::schemas::response::hold::HoldOut;

fn owner_id(headers: &HeaderMap) -> Result<String, ServiceError> {
    headers
        .get("x-user-id")
        .and_then(|value| value.to_str().ok())
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
        .ok_or(ServiceError::Unauthorized)
}

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
    State(state): State<AppState<D>>,
    headers: HeaderMap,
    body: Result<Json<HoldIn>, JsonRejection>,
) -> Result<(StatusCode, Json<HoldOut>), ServiceError> {
    let owner_id = owner_id(&headers)?;
    let Json(input) = body.map_err(|error| ServiceError::Validation(error.body_text()))?;

    let hold = state
        .hold_seats
        .execute(NewHold {
            event_id: input.event_id,
            seat_ids: input.seat_ids,
            owner_id,
        })
        .await?;
    Ok((StatusCode::CREATED, Json(hold.into())))
}
