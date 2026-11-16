import pytest
from sqlalchemy import text

from core import replication

pytestmark = pytest.mark.anyio

SLOTS = ("test_replica_a", "test_replica_b")


@pytest.fixture
async def unused_slots(async_engine):
    """Slots of replicas that are not streaming: a primary keeps one per replica."""
    async with async_engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for slot in SLOTS:
            await conn.execute(
                text("SELECT pg_create_physical_replication_slot(:slot, true)"),
                {"slot": slot},
            )
    yield
    async with async_engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for slot in SLOTS:
            await conn.execute(
                text("SELECT pg_drop_replication_slot(:slot)"), {"slot": slot}
            )


async def test_every_slot_is_reported_on_its_own(unused_slots):
    await replication.probe()

    for slot in SLOTS:
        state = replication.latest[slot]
        assert state.lag_seconds is None
        assert state.retained_bytes is not None
