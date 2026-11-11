import uuid
from typing import Annotated

from fastapi import Header, status

from core.consistency import TOKEN_PATTERN
from core.schemas import error_responses
from core.user import UserIdDep
from orders.endpoints.v1.routes.router import router
from orders.endpoints.v1.schemas.response.order import OrderOut
from orders.use_cases.get_order import GetOrderUseCaseDep


@router.get(
    "/{order_id}",
    response_model=OrderOut,
    responses=error_responses(status.HTTP_404_NOT_FOUND),
)
async def get_order(
    order_id: uuid.UUID,
    user_id: UserIdDep,
    use_case: GetOrderUseCaseDep,
    x_consistency_token: Annotated[str | None, Header(pattern=TOKEN_PATTERN)] = None,
) -> OrderOut:
    dto = await use_case.execute(
        order_id=order_id, user_id=user_id, consistency_token=x_consistency_token
    )
    return OrderOut.from_dto(dto)
