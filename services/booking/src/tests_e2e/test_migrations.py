"""Data migrations, run between their revisions. Rows are written in SQL as each
revision had them: the ORM models follow the latest schema, not the one a test goes
back to."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from core.db import async_session
from orders.use_cases.create_order import request_fingerprint
from tests_e2e.conftest import ALEMBIC_INI_PATH

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]

EXPAND = "5d78bac08591"
BACKFILL = "8a1d4c6e2b7f"
FINGERPRINT = "b4e9f2a7c1d5"


@asynccontextmanager
async def at_revision(revision: str) -> AsyncGenerator[Config]:
    """Back to `revision` for the test, and to head after it whatever happens."""
    config = Config(str(ALEMBIC_INI_PATH))
    await asyncio.to_thread(command.downgrade, config, revision)
    try:
        yield config
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")


async def insert_order(
    *,
    token: str | None,
    status: str = "pending",
    token_row: str | None = None,
    fingerprint: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    order_id, hold_id = uuid.uuid4(), uuid.uuid4()
    async with async_session() as session:
        await session.execute(
            text(
                "INSERT INTO orders (id, user_id, idempotency_key, hold_id, "
                "payment_token, status, saga_step"
                + (", request_fingerprint" if fingerprint else "")
                + ") VALUES (:id, 'user-1', :key, :hold, :token, :status, :step"
                + (", :fingerprint" if fingerprint else "")
                + ")"
            ),
            {
                "fingerprint": fingerprint,
                "id": order_id,
                "key": str(uuid.uuid4()),
                "hold": hold_id,
                "token": token,
                "status": status,
                "step": "started" if status == "pending" else "done",
            },
        )
        if token_row is not None:
            await session.execute(
                text(
                    "INSERT INTO order_payment_tokens (order_id, payment_token) "
                    "VALUES (:id, :token)"
                ),
                {"id": order_id, "token": token_row},
            )
        await session.commit()
    return order_id, hold_id


async def rows(sql: str) -> dict:
    async with async_session() as session:
        return dict((await session.execute(text(sql))).all())


async def test_backfill_gives_a_token_row_to_orders_in_flight_only():
    async with at_revision(EXPAND) as config:
        in_flight, _ = await insert_order(token="tok_in_flight")
        await insert_order(token="tok_final", status="confirmed")
        has_one, _ = await insert_order(token="tok_column", token_row="tok_row")

        # has_one already has a row: without ON CONFLICT the backfill would fail on it.
        await asyncio.to_thread(command.upgrade, config, BACKFILL)

        tokens = await rows("SELECT order_id, payment_token FROM order_payment_tokens")
        assert tokens == {in_flight: "tok_in_flight", has_one: "tok_row"}


async def test_every_order_gets_its_fingerprint_and_final_orders_lose_their_token():
    async with at_revision(BACKFILL) as config:
        in_flight, in_flight_hold = await insert_order(token="tok_a", token_row="tok_a")
        final, final_hold = await insert_order(
            token="tok_b", status="confirmed", token_row="tok_b"
        )

        await asyncio.to_thread(command.upgrade, config, "head")

        fingerprints = await rows("SELECT id, request_fingerprint FROM orders")
        assert fingerprints == {
            in_flight: request_fingerprint(
                hold_id=in_flight_hold, payment_token="tok_a"
            ),
            final: request_fingerprint(hold_id=final_hold, payment_token="tok_b"),
        }
        tokens = await rows("SELECT order_id, payment_token FROM order_payment_tokens")
        assert tokens == {in_flight: "tok_a"}


async def test_older_orders_get_a_fingerprint_and_the_column_may_be_empty():
    async with at_revision(FINGERPRINT) as config:
        without, hold = await insert_order(token="tok_a")
        final, _ = await insert_order(
            token="tok_b", status="failed", token_row="tok_b", fingerprint="kept"
        )

        await asyncio.to_thread(command.upgrade, config, "head")

        fingerprints = await rows("SELECT id, request_fingerprint FROM orders")
        assert fingerprints == {
            without: request_fingerprint(hold_id=hold, payment_token="tok_a"),
            final: "kept",
        }
        assert (
            await rows("SELECT order_id, payment_token FROM order_payment_tokens") == {}
        )
        empty, _ = await insert_order(token=None, fingerprint="new")
        column = await rows(
            f"SELECT id, payment_token FROM orders WHERE id = '{empty}'"
        )
        assert column == {empty: None}
