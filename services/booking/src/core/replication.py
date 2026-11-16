"""Replication state, read from the primary by a background job of the API process
every few seconds and exported as gauges per slot (core/telemetry.py). Runs only with a
replica (`DB_REPLICA`). A primary keeps a physical slot per replica: one in compose
(infra/replication/setup.sql), one per standby in the cluster, named by its operator.
"""

import asyncio
from contextlib import suppress

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text

from core.db import engine

# On the primary, for every replica's slot. `replay_lag`: from writing WAL here to the
# replica applying it; Postgres shows NULL once the replica has caught up and nothing
# new was written, which is 0 here. The age of the replica's last applied transaction
# is not a lag: after a quiet period it counts the quiet time too. No row in
# pg_stat_replication: the replica is not streaming, so the lag is unknown. `retained`:
# the WAL the slot keeps until the replica confirms it; it grows without limit while the
# replica is gone, until the primary's disk is full.
REPLICATION_STATE = text(
    "SELECT s.slot_name AS slot, CASE WHEN r.pid IS NULL THEN NULL "
    "ELSE extract(epoch FROM coalesce(r.replay_lag, interval '0')) END AS lag, "
    "pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn) AS retained "
    "FROM pg_replication_slots s "
    "LEFT JOIN pg_stat_replication r ON r.pid = s.active_pid "
    "WHERE s.slot_type = 'physical'"
)


class SlotState(BaseModel):
    lag_seconds: float | None
    retained_bytes: float | None


# Latest measurements, by slot; empty until the first probe or after a failed one.
latest: dict[str, SlotState] = {}


async def probe() -> None:
    async with engine.connect() as connection:
        rows = (await connection.execute(REPLICATION_STATE)).all()
    latest.clear()
    for row in rows:
        latest[row.slot] = SlotState(
            lag_seconds=None if row.lag is None else float(row.lag),
            retained_bytes=None if row.retained is None else float(row.retained),
        )


async def run(*, interval: float, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await probe()
        except Exception as exc:
            latest.clear()
            logger.bind(error=repr(exc)).warning("Replication probe failed")
        with suppress(TimeoutError):
            async with asyncio.timeout(interval):
                await stop.wait()
