import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select

from core.db import async_session, engine
from core.logging import configure_logging
from events.models.event import Event
from venues.models.venue import Venue


async def seed() -> None:
    async with async_session() as session:
        if await session.scalar(select(func.count()).select_from(Venue)):
            logger.info("Catalog already has venues, skipping seed")
            return

        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        palace = Venue(name="Palace of Ukraine", city="Kyiv")
        opera = Venue(name="Lviv Opera", city="Lviv")
        session.add_all(
            [
                palace,
                opera,
                Event(
                    venue=palace,
                    title="Rock Night",
                    starts_at=now + timedelta(days=30),
                    sales_start_at=now + timedelta(hours=1),
                ),
                Event(
                    venue=palace,
                    title="Symphony Evening",
                    starts_at=now + timedelta(days=45),
                    sales_start_at=now - timedelta(days=1),
                ),
                Event(
                    venue=opera,
                    title="La Traviata",
                    starts_at=now + timedelta(days=14),
                    sales_start_at=now - timedelta(days=7),
                ),
            ]
        )
        await session.commit()
        logger.info("Seeded 2 venues and 3 events")


async def main() -> None:
    try:
        await seed()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
