"""Work cut off before it ends, on real commits: a client that goes away from
POST /orders, and a reply whose handling runs out of its timeout while the order's row
is locked. Another connection keeps the work waiting at a known point.
"""

import asyncio
import socket
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from core.db import async_session, engine
from main import app
from orders.dto.messages import ChargeStatus
from orders.models.order import Order, SagaStep
from tests_e2e.helpers import USER, create_order, load
from tests_e2e.test_saga_finish import charge_result, deliver

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]

CLIENT_TIMEOUT = 0.2
CLEARED_AFTER = 0.5


@pytest.fixture
async def live_api() -> AsyncIterator[str]:
    """booking's app on a real uvicorn server, so a client can really go away."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await serving


async def count(query: str) -> int:
    async with engine.connect() as connection:
        return (await connection.execute(text(query))).scalar_one()


async def waiting_on_a_lock() -> int:
    return await count(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        " AND wait_event_type = 'Lock'"
    )


async def idle_in_transaction() -> int:
    return await count(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        " AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
    )


async def clear_later(blocker: AsyncConnection, waits: list[int]) -> None:
    """Notes how many sessions wait on a lock before and after the work is cut off,
    then rolls the blocker back."""
    await asyncio.sleep(0.1)
    waits.append(await waiting_on_a_lock())
    await asyncio.sleep(0.3)
    waits.append(await waiting_on_a_lock())
    await asyncio.sleep(CLEARED_AFTER - 0.4)
    await blocker.rollback()


async def orders_with_key(key: str) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Order).where(Order.idempotency_key == key)
        )
        return result.scalar_one()


async def test_an_order_whose_client_left_is_still_made_and_found_by_its_key(live_api):
    """Starlette does not cancel an endpoint when its client goes away: the order is
    made for nobody, and the key, which POST /orders requires, finds it again."""
    key = str(uuid.uuid4())
    body = {"hold_id": str(uuid.uuid4()), "payment_token": "tok_ok"}
    headers = {"X-User-Id": USER, "Idempotency-Key": key}
    async with engine.connect() as blocker:
        await blocker.execute(
            text(
                "INSERT INTO orders (user_id, idempotency_key, hold_id, status)"
                " VALUES (:user, :key, :hold, 'failed')"
            ),
            {"user": USER, "key": key, "hold": uuid.uuid4()},
        )
        waits: list[int] = []
        clearing = asyncio.create_task(clear_later(blocker, waits))
        async with httpx.AsyncClient(base_url=live_api) as client:
            with pytest.raises(httpx.TimeoutException):
                await client.post(
                    "/api/v1/orders", json=body, headers=headers, timeout=CLIENT_TIMEOUT
                )
            await clearing
            await asyncio.sleep(0.5)
            made = await orders_with_key(key)
            repeat = await client.post("/api/v1/orders", json=body, headers=headers)

    assert waits == [1, 1], "the insert waited, and still waits with its client gone"
    assert made == 1, "the endpoint ran on and made the order"
    assert repeat.status_code == 202
    assert await idle_in_transaction() == 0


async def test_a_reply_cut_off_by_its_timeout_leaves_no_query_or_transaction_behind():
    """asyncio's timeout cancels the handler while the order's row is locked: the reply
    goes back to the queue, the order is unchanged, and nothing is left running."""
    order_id = await create_order(saga_step=SagaStep.AWAITING_CHARGE)
    async with engine.connect() as blocker:
        await blocker.execute(
            text("SELECT 1 FROM orders WHERE id = :id FOR UPDATE"), {"id": order_id}
        )
        waits: list[int] = []
        clearing = asyncio.create_task(clear_later(blocker, waits))
        settled = await deliver(
            charge_result(order_id, ChargeStatus.SUCCEEDED),
            REPLY_TIMEOUT_SECONDS=CLIENT_TIMEOUT,
            RETRY_BASE_DELAY_SECONDS=0,
        )
        await clearing

    assert settled == ["retry"]
    assert waits == [1, 0], "the waiting query was cancelled in Postgres too"
    assert (await load(order_id)).saga_step == SagaStep.AWAITING_CHARGE
    assert await idle_in_transaction() == 0
    assert await deliver(charge_result(order_id, ChargeStatus.SUCCEEDED)) == ["ack"]
    assert (await load(order_id)).saga_step == SagaStep.CHARGED
