import uuid

import pytest
from fastapi import status


@pytest.mark.anyio
async def test_get_event_404_on_missing_event(async_client):
    response = await async_client.get(f"/api/v1/events/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["code"] == "not_found"


@pytest.mark.anyio
async def test_get_event_returns_event_with_venue(async_client, event_factory):
    event = await event_factory()

    response = await async_client.get(f"/api/v1/events/{event.id}")
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["id"] == str(event.id)
    assert body["title"] == event.title
    assert body["currency"] == event.currency
    assert body["venue"]["id"] == str(event.venue.id)


@pytest.mark.anyio
async def test_get_event_422_on_malformed_id(async_client):
    response = await async_client.get("/api/v1/events/not-a-uuid")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
