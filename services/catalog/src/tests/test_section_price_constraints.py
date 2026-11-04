import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from events.models.event import Event


@pytest.mark.anyio
async def test_accepts_section_of_event_venue(section_price_factory):
    price = await section_price_factory()
    assert price.price_minor == 150000


@pytest.mark.anyio
async def test_rejects_section_of_another_venue(
    event_factory, section_factory, section_price_factory
):
    event = await event_factory()
    foreign_section = await section_factory()

    with pytest.raises(IntegrityError, match="fk_section_prices_section_id_sections"):
        await section_price_factory(event=event, section=foreign_section)


@pytest.mark.anyio
async def test_rejects_venue_id_taken_from_foreign_section(
    event_factory, section_factory, section_price_factory
):
    event = await event_factory()
    foreign_section = await section_factory()

    with pytest.raises(IntegrityError, match="fk_section_prices_event_id_events"):
        await section_price_factory(
            event=event, section=foreign_section, venue_id=foreign_section.venue_id
        )


@pytest.mark.anyio
async def test_rejects_moving_priced_event_to_another_venue(
    db_session, venue_factory, event_factory, section_factory, section_price_factory
):
    event = await event_factory()
    section = await section_factory(venue=event.venue)
    await section_price_factory(event=event, section=section)
    other_venue = await venue_factory()

    with pytest.raises(IntegrityError, match="fk_section_prices_event_id_events"):
        await db_session.execute(
            update(Event).where(Event.id == event.id).values(venue_id=other_venue.id)
        )


@pytest.mark.anyio
async def test_rejects_negative_price(section_price_factory):
    with pytest.raises(IntegrityError, match="price_minor_non_negative"):
        await section_price_factory(price_minor=-1)
