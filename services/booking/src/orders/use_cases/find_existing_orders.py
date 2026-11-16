import uuid
from typing import Annotated

from fastapi import Depends

from orders.repositories.order import OrderRepository, OrderRepositoryDep


class FindExistingOrdersUseCase:
    def __init__(self, *, order_repository: OrderRepository):
        self.order_repository = order_repository

    async def execute(self, *, order_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Reads the primary: a lagging replica would report a new order as missing."""
        return await self.order_repository.existing_ids(order_ids=order_ids)


def get_find_existing_orders_use_case(
    order_repository: OrderRepositoryDep,
) -> FindExistingOrdersUseCase:
    return FindExistingOrdersUseCase(order_repository=order_repository)


FindExistingOrdersUseCaseDep = Annotated[
    FindExistingOrdersUseCase, Depends(get_find_existing_orders_use_case)
]
