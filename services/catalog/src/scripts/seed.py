import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select

from core.db import async_session, engine
from core.logging import configure_logging
from events.models.event import Event
from events.models.section_price import SectionPrice
from venues.models.seat import Seat
from venues.models.section import Section
from venues.models.venue import Venue


def build_seats(*, section: Section, rows: int, per_row: int) -> list[Seat]:
    return [
        Seat(section=section, row=row, number=number)
        for row in range(1, rows + 1)
        for number in range(1, per_row + 1)
    ]


def build_price(*, event: Event, section: Section, price_minor: int) -> SectionPrice:
    return SectionPrice(
        event_id=event.id,
        section_id=section.id,
        venue_id=event.venue_id,
        price_minor=price_minor,
    )


async def seed() -> None:
    async with async_session() as session:
        if await session.scalar(select(func.count()).select_from(Venue)):
            logger.info("Catalog already has venues, skipping seed")
            return

        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        palace = Venue(name="Palace of Ukraine", city="Kyiv")
        opera = Venue(name="Lviv Opera", city="Lviv")
        palace_parter = Section(venue=palace, name="Parter")
        palace_balcony = Section(venue=palace, name="Balcony")
        opera_parter = Section(venue=opera, name="Parter")
        rock_night = Event(
            venue=palace,
            title="Rock Night",
            starts_at=now + timedelta(days=30),
            sales_start_at=now + timedelta(hours=1),
            currency="UAH",
        )
        symphony = Event(
            venue=palace,
            title="Symphony Evening",
            starts_at=now + timedelta(days=45),
            sales_start_at=now - timedelta(days=1),
            currency="UAH",
        )
        traviata = Event(
            venue=opera,
            title="La Traviata",
            starts_at=now + timedelta(days=14),
            sales_start_at=now - timedelta(days=7),
            currency="UAH",
        )
        session.add_all(
            [
                rock_night,
                symphony,
                traviata,
                *build_seats(section=palace_parter, rows=10, per_row=20),
                *build_seats(section=palace_balcony, rows=5, per_row=20),
                *build_seats(section=opera_parter, rows=8, per_row=15),
            ]
        )
        await session.flush()

        session.add_all(
            [
                build_price(
                    event=rock_night, section=palace_parter, price_minor=150000
                ),
                build_price(
                    event=rock_night, section=palace_balcony, price_minor=60000
                ),
                build_price(event=symphony, section=palace_parter, price_minor=90000),
                build_price(event=traviata, section=opera_parter, price_minor=120000),
            ]
        )
        await session.commit()
        logger.info("Seeded 2 venues, 3 events, 3 sections, 420 seats and 4 prices")


async def main() -> None:
    try:
        await seed()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
