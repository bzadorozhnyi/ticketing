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
        "currency": "UAH",
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
        {
            "id": str(balcony.id),
            "name": "Balcony",
            "seat_count": 0,
            "price_minor": None,
        },
        {
            "id": str(parter.id),
            "name": "Parter",
            "seat_count": 2,
            "price_minor": None,
        },
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


@pytest.mark.anyio
async def test_list_event_sections_returns_event_currency_and_prices(
    async_client, event_factory, section_factory, section_price_factory
):
    event = await event_factory(currency="EUR")
    priced = await section_factory(venue=event.venue, name="A")
    unpriced = await section_factory(venue=event.venue, name="B")
    await section_price_factory(event=event, section=priced, price_minor=150000)

    response = await async_client.get(f"/api/v1/events/{event.id}/sections")
    body = response.json()
    assert body["currency"] == "EUR"
    prices = {s["id"]: s["price_minor"] for s in body["sections"]}
    assert prices == {str(priced.id): 150000, str(unpriced.id): None}


@pytest.mark.anyio
async def test_list_event_sections_ignores_prices_of_other_events(
    async_client, venue_factory, event_factory, section_factory, section_price_factory
):
    venue = await venue_factory()
    event = await event_factory(venue=venue)
    other_event = await event_factory(venue=venue)
    section = await section_factory(venue=venue)
    await section_price_factory(event=other_event, section=section, price_minor=99)

    response = await async_client.get(f"/api/v1/events/{event.id}/sections")
    assert response.json()["sections"][0]["price_minor"] is None
