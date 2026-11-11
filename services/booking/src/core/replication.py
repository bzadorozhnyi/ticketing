"""Replication state, read from the primary by a background job of the API process every
few seconds and exported as gauges (core/telemetry.py). Runs only with a replica
(`DB_REPLICA`).
"""

import asyncio
from contextlib import suppress

from loguru import logger
from sqlalchemy import text

from core.db import engine

# On the primary, for the replica's slot. `replay_lag`: from writing WAL here to the
# replica applying it; Postgres shows NULL once the replica has caught up and nothing
# new was written, which is 0 here. The age of the replica's last applied transaction
# is not a lag: after a quiet period it counts the quiet time too. No row in
# pg_stat_replication: the replica is not streaming, so the lag is unknown. `retained`:
# the WAL the slot keeps until the replica confirms it; it grows without limit while the
# replica is gone, until the primary's disk is full.
REPLICATION_STATE = text(
    "SELECT CASE WHEN r.pid IS NULL THEN NULL "
    "ELSE extract(epoch FROM coalesce(r.replay_lag, interval '0')) END AS lag, "
    "pg_wal_lsn_diff(pg_current_wal_lsn(), s.restart_lsn) AS retained "
    "FROM pg_replication_slots s "
    "LEFT JOIN pg_stat_replication r ON r.pid = s.active_pid "
    "WHERE s.slot_name = :slot"
)

# Latest measurements, by name; empty until the first probe or after a failed one.
latest: dict[str, float] = {}


async def probe(*, slot: str) -> None:
    async with engine.connect() as connection:
        row = (await connection.execute(REPLICATION_STATE, {"slot": slot})).first()
    latest.clear()
    if row is None:
        return
    if row.lag is not None:
        latest["lag_seconds"] = float(row.lag)
    if row.retained is not None:
        latest["retained_bytes"] = float(row.retained)


async def run(*, interval: float, slot: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await probe(slot=slot)
        except Exception as exc:
            latest.clear()
            logger.bind(error=repr(exc)).warning("Replication probe failed")
        with suppress(TimeoutError):
            async with asyncio.timeout(interval):
                await stop.wait()
