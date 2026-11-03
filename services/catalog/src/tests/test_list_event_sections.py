import uuid

import pytest
from fastapi import status


@pytest.mark.anyio
async def test_list_event_sections_404_on_missing_event(async_client):
    response = await async_client.get(f"/api/v1/events/{uuid.uuid4()}/sections")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_list_event_sections_empty_venue(async_client, event_factory):
    event = await event_factory()

    response = await async_client.get(f"/api/v1/events/{event.id}/sections")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "event_id": str(event.id),
        "venue_id": str(event.venue.id),
        "sections": [],
    }


@pytest.mark.anyio
async def test_list_event_sections_sorted_by_name_with_seat_counts(
    async_client, event_factory, section_factory, seat_factory
):
    event = await event_factory()
    parter = await section_factory(venue=event.venue, name="Parter")
    balcony = await section_factory(venue=event.venue, name="Balcony")
    await seat_factory(section=parter)
    await seat_factory(section=parter)

    response = await async_client.get(f"/api/v1/events/{event.id}/sections")
    assert response.json()["sections"] == [
        {"id": str(balcony.id), "name": "Balcony", "seat_count": 0},
        {"id": str(parter.id), "name": "Parter", "seat_count": 2},
    ]


@pytest.mark.anyio
async def test_list_event_sections_excludes_other_venues(
    async_client, event_factory, section_factory
):
    event = await event_factory()
    own = await section_factory(venue=event.venue)
    await section_factory()

    response = await async_client.get(f"/api/v1/events/{event.id}/sections")
    assert [s["id"] for s in response.json()["sections"]] == [str(own.id)]
