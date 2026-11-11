import uuid
from typing import Annotated

from fastapi import Depends

from core.consistency import ReplicaReads
from core.exceptions import NotFoundException
from orders.dto.order import OrderDTO
from orders.repositories.order import OrderReadsDep, OrderRepository


class GetOrderUseCase:
    def __init__(self, *, reads: ReplicaReads[OrderRepository]):
        self.reads = reads

    async def execute(
        self, *, order_id: uuid.UUID, user_id: str, consistency_token: str | None
    ) -> OrderDTO:
        """Reads from the replica. With the token of the client's last write, the client
        sees that write; without it, an order the replica does not have yet is read
        from the primary, so a new order is never reported as missing.
        """
        order = await self.reads.read(
            lambda orders: orders.get_for_user(order_id=order_id, user_id=user_id),
            token=consistency_token,
        )
        if order is None:
            raise NotFoundException(f"Order {order_id} not found")
        return OrderDTO.from_model(order)


def get_get_order_use_case(reads: OrderReadsDep) -> GetOrderUseCase:
    return GetOrderUseCase(reads=reads)


GetOrderUseCaseDep = Annotated[GetOrderUseCase, Depends(get_get_order_use_case)]
