import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.db import SessionDep
from events.models.event import Event


class EventRepository:
    def __init__(self, *, session: AsyncSession):
        self.session = session

    async def get_page(self, *, limit: int, offset: int) -> list[Event]:
        result = await self.session.execute(
            select(Event)
            .options(joinedload(Event.venue))
            .order_by(Event.starts_at, Event.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_id(self, *, event_id: uuid.UUID) -> Event | None:
        result = await self.session.execute(
            select(Event).options(joinedload(Event.venue)).where(Event.id == event_id)
        )
        return result.scalar_one_or_none()


def get_event_repository(session: SessionDep) -> EventRepository:
    return EventRepository(session=session)


EventRepositoryDep = Annotated[EventRepository, Depends(get_event_repository)]
