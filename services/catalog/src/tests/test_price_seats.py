import uuid

import pytest
from fastapi import status


def price_url(event_id) -> str:
    return f"/internal/v1/events/{event_id}/price"


@pytest.mark.anyio
async def test_sums_the_prices_of_seats_in_different_sections(
    async_client, event_factory, section_factory, section_price_factory, seat_factory
):
    event = await event_factory()
    parter = await section_factory(venue=event.venue)
    balcony = await section_factory(venue=event.venue)
    await section_price_factory(event=event, section=parter, price_minor=150_000)
    await section_price_factory(event=event, section=balcony, price_minor=50_000)
    seats = [
        await seat_factory(section=parter),
        await seat_factory(section=parter),
        await seat_factory(section=balcony),
    ]

    response = await async_client.post(
        price_url(event.id), json={"seat_ids": [str(seat.id) for seat in seats]}
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"amount_minor": 350_000, "currency": "UAH"}


@pytest.mark.anyio
async def test_the_price_belongs_to_the_event(
    async_client, event_factory, section_factory, section_price_factory, seat_factory
):
    event = await event_factory()
    other_event = await event_factory(venue=event.venue)
    section = await section_factory(venue=event.venue)
    await section_price_factory(event=event, section=section, price_minor=150_000)
    await section_price_factory(event=other_event, section=section, price_minor=90_000)
    seat = await seat_factory(section=section)

    for event_id, expected in [(event.id, 150_000), (other_event.id, 90_000)]:
        response = await async_client.post(
            price_url(event_id), json={"seat_ids": [str(seat.id)]}
        )

        assert response.json()["amount_minor"] == expected


@pytest.mark.anyio
async def test_a_seat_without_a_price_is_refused(
    async_client, event_factory, section_factory, section_price_factory, seat_factory
):
    event = await event_factory()
    priced = await section_factory(venue=event.venue)
    unpriced = await section_factory(venue=event.venue)
    await section_price_factory(event=event, section=priced)
    good = await seat_factory(section=priced)
    bad = await seat_factory(section=unpriced)

    response = await async_client.post(
        price_url(event.id), json={"seat_ids": [str(good.id), str(bad.id)]}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["details"] == [str(bad.id)]


@pytest.mark.anyio
async def test_a_seat_of_another_venue_is_refused(
    async_client, event_factory, section_price_factory, seat_factory
):
    event = await event_factory()
    await section_price_factory(event=event)
    foreign_seat = await seat_factory()

    response = await async_client.post(
        price_url(event.id), json={"seat_ids": [str(foreign_seat.id)]}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_an_unknown_event_is_refused(async_client):
    event_id = uuid.uuid4()

    response = await async_client.post(
        price_url(event_id), json={"seat_ids": [str(uuid.uuid4())]}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["message"] == f"Event {event_id} not found"


@pytest.mark.anyio
async def test_an_empty_list_is_refused(async_client, event_factory):
    event = await event_factory()

    response = await async_client.post(price_url(event.id), json={"seat_ids": []})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
