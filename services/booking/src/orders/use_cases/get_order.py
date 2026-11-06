import uuid
from typing import Annotated

from fastapi import Depends

from core.exceptions import NotFoundException
from orders.dto.order import OrderDTO
from orders.repositories.order import OrderRepository, OrderRepositoryDep


class GetOrderUseCase:
    def __init__(self, *, order_repository: OrderRepository):
        self.order_repository = order_repository

    async def execute(self, *, order_id: uuid.UUID, user_id: str) -> OrderDTO:
        order = await self.order_repository.get_for_user(
            order_id=order_id, user_id=user_id
        )
        if order is None:
            raise NotFoundException(f"Order {order_id} not found")
        return OrderDTO.from_model(order)


def get_get_order_use_case(order_repository: OrderRepositoryDep) -> GetOrderUseCase:
    return GetOrderUseCase(order_repository=order_repository)


GetOrderUseCaseDep = Annotated[GetOrderUseCase, Depends(get_get_order_use_case)]
