import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import SessionDep
from orders.models.order import Order, OrderStatus


class OrderRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_for_user(self, *, order_id: uuid.UUID, user_id: str) -> Order | None:
        result = await self.session.execute(
            select(Order).where(Order.id == order_id, Order.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_key(self, *, user_id: str, idempotency_key: str) -> Order | None:
        result = await self.session.execute(
            select(Order).where(
                Order.user_id == user_id, Order.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def insert_pending_if_absent(
        self,
        *,
        user_id: str,
        idempotency_key: str,
        hold_id: uuid.UUID,
        payment_token: str,
    ) -> None:
        """No conflict target on purpose: either unique index refuses the row.
        Of two concurrent calls one inserts, the other waits for it and does nothing.
        """
        await self.session.execute(
            insert(Order)
            .values(
                user_id=user_id,
                idempotency_key=idempotency_key,
                hold_id=hold_id,
                payment_token=payment_token,
                status=OrderStatus.PENDING,
            )
            .on_conflict_do_nothing()
        )


def get_order_repository(session: SessionDep) -> OrderRepository:
    return OrderRepository(session=session)


OrderRepositoryDep = Annotated[OrderRepository, Depends(get_order_repository)]
