"""Verifies the test infra itself: rollback isolation between tests and that
factories don't over-generate when overridden. Not feature coverage.
"""

import pytest
from sqlalchemy import func, select

from events.models.event import Event
from venues.models.venue import Venue


@pytest.mark.anyio
async def test_db_is_empty_at_test_start(db_session):
    """Confirms rollback-per-test isolation is real, not accidental."""
    count = await db_session.scalar(select(func.count()).select_from(Venue))
    assert count == 0


@pytest.mark.anyio
async def test_venue_factory_gets_server_generated_uuidv7(venue_factory):
    venue = await venue_factory()
    assert venue.id.version == 7
    assert venue.created_at.tzinfo is not None


@pytest.mark.anyio
async def test_event_factory_default_creates_one_venue(db_session, event_factory):
    await event_factory()

    venue_count = await db_session.scalar(select(func.count()).select_from(Venue))
    event_count = await db_session.scalar(select(func.count()).select_from(Event))
    assert venue_count == 1
    assert event_count == 1


@pytest.mark.anyio
async def test_event_factory_reuses_explicit_venue_override(
    db_session, venue_factory, event_factory
):
    venue = await venue_factory()

    await event_factory(venue=venue)
    await event_factory(venue=venue)

    venue_count = await db_session.scalar(select(func.count()).select_from(Venue))
    event_count = await db_session.scalar(select(func.count()).select_from(Event))
    assert venue_count == 1  # not 3 — the override was actually honored
    assert event_count == 2
