import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status


def url(event_id, section_id) -> str:
    return f"/api/v1/events/{event_id}/sections/{section_id}/availability"


@pytest.mark.anyio
async def test_lists_sold_and_held_seats_by_row_and_number(
    async_client,
    event_factory,
    section_factory,
    seat_factory,
    seat_availability_factory,
):
    event = await event_factory()
    section = await section_factory(venue=event.venue)
    first, second, free = [
        await seat_factory(section=section, row=1, number=n) for n in (1, 2, 3)
    ]
    await seat_availability_factory(
        event=event, seat=second, status="sold", expires_at=None
    )
    await seat_availability_factory(event=event, seat=first)

    response = await async_client.get(url(event.id, section.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "event_id": str(event.id),
        "section_id": str(section.id),
        "taken_seat_ids": [str(first.id), str(second.id)],
    }


@pytest.mark.anyio
async def test_a_released_or_expired_hold_and_another_event_leave_a_seat_free(
    async_client,
    event_factory,
    section_factory,
    seat_factory,
    seat_availability_factory,
):
    event = await event_factory()
    other_event = await event_factory(venue=event.venue)
    section = await section_factory(venue=event.venue)
    released, expired, held_elsewhere = [
        await seat_factory(section=section) for _ in range(3)
    ]
    await seat_availability_factory(
        event=event, seat=released, status="free", expires_at=None
    )
    await seat_availability_factory(
        event=event, seat=expired, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    await seat_availability_factory(event=other_event, seat=held_elsewhere)

    response = await async_client.get(url(event.id, section.id))

    assert response.json()["taken_seat_ids"] == []


@pytest.mark.anyio
async def test_404_for_a_missing_event_or_a_section_of_another_venue(
    async_client, event_factory, section_factory
):
    event = await event_factory()
    elsewhere = await section_factory()

    missing_event = await async_client.get(url(uuid.uuid4(), elsewhere.id))
    foreign_section = await async_client.get(url(event.id, elsewhere.id))

    assert missing_event.status_code == status.HTTP_404_NOT_FOUND
    assert foreign_section.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_sections_count_their_available_seats(
    async_client,
    event_factory,
    section_factory,
    seat_factory,
    seat_availability_factory,
):
    event = await event_factory()
    section = await section_factory(venue=event.venue)
    sold, held, released, _ = [await seat_factory(section=section) for _ in range(4)]
    await seat_availability_factory(
        event=event, seat=sold, status="sold", expires_at=None
    )
    await seat_availability_factory(event=event, seat=held)
    await seat_availability_factory(
        event=event, seat=released, status="free", expires_at=None
    )

    response = await async_client.get(f"/api/v1/events/{event.id}/sections")

    [listed] = response.json()["sections"]
    assert (listed["seat_count"], listed["available_count"]) == (4, 2)
