import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select

from core.db import async_session, engine
from core.logging import configure_logging
from events.models.event import Event
from venues.models.seat import Seat
from venues.models.section import Section
from venues.models.venue import Venue


def build_section(
    *, venue: Venue, name: str, rows: int, per_row: int
) -> list[Section | Seat]:
    section = Section(venue=venue, name=name)
    seats = [
        Seat(section=section, row=row, number=number)
        for row in range(1, rows + 1)
        for number in range(1, per_row + 1)
    ]
    return [section, *seats]


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
                *build_section(venue=palace, name="Parter", rows=10, per_row=20),
                *build_section(venue=palace, name="Balcony", rows=5, per_row=20),
                *build_section(venue=opera, name="Parter", rows=8, per_row=15),
            ]
        )
        await session.commit()
        logger.info("Seeded 2 venues, 3 events, 3 sections and 420 seats")


async def main() -> None:
    try:
        await seed()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
