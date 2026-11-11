from typing import Annotated

from fastapi import Header, Response, status

from core.consistency import TOKEN_HEADER
from core.schemas import error_responses
from core.user import UserIdDep
from orders.endpoints.v1.routes.router import router
from orders.endpoints.v1.schemas.request.order import CreateOrderIn
from orders.endpoints.v1.schemas.response.order import OrderOut
from orders.use_cases.create_order import CreateOrderUseCaseDep


@router.post(
    "",
    response_model=OrderOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "headers": {
                TOKEN_HEADER: {
                    "description": "Send it with GET /orders/{order_id} to see this "
                    "order at once, even on a replica that lags behind",
                    "schema": {"type": "string"},
                }
            }
        },
        **error_responses(status.HTTP_409_CONFLICT),
    },
)
async def create_order(
    body: CreateOrderIn,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=255)],
    user_id: UserIdDep,
    use_case: CreateOrderUseCaseDep,
) -> OrderOut:
    created = await use_case.execute(
        user_id=user_id,
        idempotency_key=idempotency_key,
        hold_id=body.hold_id,
        payment_token=body.payment_token,
    )
    response.headers[TOKEN_HEADER] = created.consistency_token
    return OrderOut.from_dto(created.order)
