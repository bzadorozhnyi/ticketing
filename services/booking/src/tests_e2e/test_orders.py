import asyncio
import uuid
from collections import Counter

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from core.db import async_session
from orders.models.order import Order

pytestmark = pytest.mark.anyio

ORDERS_URL = "/api/v1/orders"
CLIENTS = 50


@pytest.fixture
async def client(asgi_app):
    transport = ASGITransport(app=asgi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def orders_on_hold(hold_id: uuid.UUID):
    async with async_session() as session:
        return await session.scalar(
            select(func.count()).select_from(Order).where(Order.hold_id == hold_id)
        )


def create(client: AsyncClient, *, hold_id: uuid.UUID, key: str, user: str = "user-1"):
    return client.post(
        ORDERS_URL,
        json={"hold_id": str(hold_id), "payment_token": "tok_ok"},
        headers={"Idempotency-Key": key, "X-User-Id": user},
    )


async def test_concurrent_repeats_create_one_order(client):
    hold_id = uuid.uuid4()

    responses = await asyncio.gather(
        *(create(client, hold_id=hold_id, key="same-key") for _ in range(CLIENTS))
    )

    assert Counter(r.status_code for r in responses) == {
        status.HTTP_202_ACCEPTED: CLIENTS
    }
    assert len({r.json()["id"] for r in responses}) == 1
    assert await orders_on_hold(hold_id) == 1


async def test_concurrent_orders_on_one_hold_leave_one_winner(client):
    hold_id = uuid.uuid4()

    responses = await asyncio.gather(
        *(create(client, hold_id=hold_id, key=f"key-{n}") for n in range(CLIENTS))
    )

    assert Counter(r.status_code for r in responses) == {
        status.HTTP_202_ACCEPTED: 1,
        status.HTTP_409_CONFLICT: CLIENTS - 1,
    }
    assert await orders_on_hold(hold_id) == 1
