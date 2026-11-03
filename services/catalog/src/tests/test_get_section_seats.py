import uuid

import pytest
from fastapi import status


@pytest.mark.anyio
async def test_get_section_seats_404_on_missing_event(async_client, section_factory):
    section = await section_factory()

    response = await async_client.get(
        f"/api/v1/events/{uuid.uuid4()}/sections/{section.id}/seats"
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_section_seats_404_on_missing_section(async_client, event_factory):
    event = await event_factory()

    response = await async_client.get(
        f"/api/v1/events/{event.id}/sections/{uuid.uuid4()}/seats"
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_section_seats_404_on_section_of_another_venue(
    async_client, event_factory, section_factory
):
    event = await event_factory()
    foreign_section = await section_factory()

    response = await async_client.get(
        f"/api/v1/events/{event.id}/sections/{foreign_section.id}/seats"
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_section_seats_ordered_by_row_and_number(
    async_client, event_factory, section_factory, seat_factory
):
    event = await event_factory()
    section = await section_factory(venue=event.venue)
    for row, number in [(2, 2), (2, 1), (1, 2), (1, 1)]:
        await seat_factory(section=section, row=row, number=number)

    response = await async_client.get(
        f"/api/v1/events/{event.id}/sections/{section.id}/seats"
    )
    assert response.status_code == status.HTTP_200_OK
    seats = [(s["row"], s["number"]) for s in response.json()["seats"]]
    assert seats == [(1, 1), (1, 2), (2, 1), (2, 2)]


@pytest.mark.anyio
async def test_get_section_seats_excludes_other_sections(
    async_client, event_factory, section_factory, seat_factory
):
    event = await event_factory()
    section = await section_factory(venue=event.venue)
    other = await section_factory(venue=event.venue)
    own_seat = await seat_factory(section=section)
    await seat_factory(section=other)

    response = await async_client.get(
        f"/api/v1/events/{event.id}/sections/{section.id}/seats"
    )
    assert [s["id"] for s in response.json()["seats"]] == [str(own_seat.id)]
