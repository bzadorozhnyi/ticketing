import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import status


@pytest.mark.anyio
async def test_list_events_empty(async_client):
    response = await async_client.get("/api/v1/events")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"items": [], "limit": 50, "offset": 0}


@pytest.mark.anyio
async def test_list_events_ordered_by_starts_at(async_client, event_factory):
    now = datetime.now(UTC)
    later = await event_factory(starts_at=now + timedelta(days=10))
    sooner = await event_factory(starts_at=now + timedelta(days=5))

    response = await async_client.get("/api/v1/events")
    assert response.status_code == status.HTTP_200_OK
    ids = [item["id"] for item in response.json()["items"]]
    assert ids == [str(sooner.id), str(later.id)]


@pytest.mark.anyio
async def test_list_events_includes_venue(async_client, event_factory):
    event = await event_factory()

    response = await async_client.get("/api/v1/events")
    venue = response.json()["items"][0]["venue"]
    assert venue == {
        "id": str(event.venue.id),
        "name": event.venue.name,
        "city": event.venue.city,
    }


@pytest.mark.anyio
async def test_list_events_breaks_starts_at_ties_by_id(
    async_client, venue_factory, event_factory
):
    venue = await venue_factory()
    starts_at = datetime.now(UTC) + timedelta(days=7)
    ids = sorted(uuid.uuid7() for _ in range(4))
    for event_id in reversed(ids):
        await event_factory(id=event_id, venue=venue, starts_at=starts_at)

    seen = []
    for offset in range(0, 4, 2):
        response = await async_client.get(
            "/api/v1/events", params={"limit": 2, "offset": offset}
        )
        seen += [item["id"] for item in response.json()["items"]]

    assert seen == [str(event_id) for event_id in ids]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}], ids=str
)
async def test_list_events_rejects_invalid_pagination(async_client, params):
    response = await async_client.get("/api/v1/events", params=params)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
