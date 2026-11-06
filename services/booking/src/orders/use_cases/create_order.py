import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from core.exceptions import ConflictException, ValidationException
from orders.dto.order import OrderDTO
from orders.repositories.order import OrderRepository, OrderRepositoryDep


class CreateOrderUseCase:
    def __init__(self, *, session: AsyncSession, order_repository: OrderRepository):
        self.session = session
        self.order_repository = order_repository

    async def execute(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        hold_id: uuid.UUID,
        payment_token: str,
    ) -> OrderDTO:
        """Safe to repeat: one transaction, so a crash leaves either nothing or the
        order, and a repeat with the same key returns that order in any status.
        """
        await self.order_repository.insert_pending_if_absent(
            user_id=user_id,
            idempotency_key=idempotency_key,
            hold_id=hold_id,
            payment_token=payment_token,
        )
        await self.session.commit()

        order = await self.order_repository.get_by_key(
            user_id=user_id, idempotency_key=idempotency_key
        )
        if order is None:
            raise ConflictException(f"Hold {hold_id} already has an active order")
        if order.hold_id != hold_id or order.payment_token != payment_token:
            raise ValidationException("Idempotency-Key was used with another request")
        return OrderDTO.from_model(order)


def get_create_order_use_case(
    session: SessionDep, order_repository: OrderRepositoryDep
) -> CreateOrderUseCase:
    return CreateOrderUseCase(session=session, order_repository=order_repository)


CreateOrderUseCaseDep = Annotated[
    CreateOrderUseCase, Depends(get_create_order_use_case)
]
