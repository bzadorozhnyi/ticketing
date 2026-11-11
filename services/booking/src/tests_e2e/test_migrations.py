import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from core.db import async_session
from orders.models.order import OrderStatus, SagaStep
from orders.models.payment_token import OrderPaymentToken
from tests_e2e.conftest import ALEMBIC_INI_PATH
from tests_e2e.helpers import create_order

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("empty_tables")]

EXPAND = "5d78bac08591"


async def token_rows() -> dict:
    async with async_session() as session:
        rows = await session.execute(
            select(OrderPaymentToken.order_id, OrderPaymentToken.payment_token)
        )
        return dict(rows.all())


async def test_backfill_gives_a_token_row_to_orders_in_flight_only():
    config = Config(str(ALEMBIC_INI_PATH))
    await asyncio.to_thread(command.downgrade, config, EXPAND)
    in_flight = await create_order(token="tok_in_flight", without_token_row=True)
    await create_order(
        token="tok_final",
        without_token_row=True,
        status=OrderStatus.CONFIRMED,
        saga_step=SagaStep.DONE,
    )
    has_one = await create_order(token="tok_column", stored_token="tok_row")

    # has_one already has a row: without ON CONFLICT the backfill would fail on it.
    await asyncio.to_thread(command.upgrade, config, "head")

    assert await token_rows() == {in_flight: "tok_in_flight", has_one: "tok_row"}
