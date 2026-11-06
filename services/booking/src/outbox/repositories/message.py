import uuid
from collections.abc import Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from outbox.models.message import OutboxMessage


class OutboxRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def add(self, *, queue: str, body: str, traceparent: str | None) -> None:
        await self.session.execute(
            insert(OutboxMessage).values(
                queue=queue, body=body, traceparent=traceparent
            )
        )

    async def lock_batch(self, *, limit: int) -> Sequence[OutboxMessage]:
        """Oldest first. A row locked by another relay is skipped, not waited for."""
        result = await self.session.execute(
            select(OutboxMessage)
            .order_by(OutboxMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return result.scalars().all()

    async def delete(self, *, ids: Sequence[uuid.UUID]) -> None:
        await self.session.execute(
            delete(OutboxMessage).where(OutboxMessage.id.in_(ids))
        )
