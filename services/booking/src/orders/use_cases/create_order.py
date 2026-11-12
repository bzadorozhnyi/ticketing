import hashlib
import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.consistency import write_position
from core.db import SessionDep
from core.exceptions import ConflictException, ValidationException
from orders.dto.order import CreatedOrderDTO, OrderDTO
from orders.repositories.order import OrderRepository, OrderRepositoryDep


def request_fingerprint(*, hold_id: uuid.UUID, payment_token: str) -> str:
    """What makes two requests with one Idempotency-Key the same. Migration
    b4e9f2a7c1d5 computes it in SQL the same way."""
    return hashlib.sha256(f"{hold_id}:{payment_token}".encode()).hexdigest()


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
    ) -> CreatedOrderDTO:
        """Safe to repeat: one transaction, so a crash leaves either nothing or the
        order, and a repeat with the same key returns that order in any status. The
        consistency token lets the client read the order from the replica at once.
        """
        fingerprint = request_fingerprint(hold_id=hold_id, payment_token=payment_token)
        await self.order_repository.insert_pending_if_absent(
            user_id=user_id,
            idempotency_key=idempotency_key,
            hold_id=hold_id,
            payment_token=payment_token,
            request_fingerprint=fingerprint,
        )
        await self.session.commit()

        order = await self.order_repository.get_by_key(
            user_id=user_id, idempotency_key=idempotency_key
        )
        if order is None:
            raise ConflictException(f"Hold {hold_id} already has an active order")
        if order.request_fingerprint != fingerprint:
            raise ValidationException("Idempotency-Key was used with another request")
        return CreatedOrderDTO(
            order=OrderDTO.from_model(order),
            consistency_token=await write_position(self.session),
        )


def get_create_order_use_case(
    session: SessionDep, order_repository: OrderRepositoryDep
) -> CreateOrderUseCase:
    return CreateOrderUseCase(session=session, order_repository=order_repository)


CreateOrderUseCaseDep = Annotated[
    CreateOrderUseCase, Depends(get_create_order_use_case)
]
