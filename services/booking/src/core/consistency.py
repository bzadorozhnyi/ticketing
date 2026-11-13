"""Read-your-writes on the replica, see ADR 0007.

A write returns the primary's WAL position after its commit as a consistency token.
A read that brings the token goes to the replica if the replica has applied that
position, and to the primary otherwise. A read without a token goes to the replica, and
to the primary only when the replica does not have the row yet. A replica that cannot be
reached, or that cancels the read, sends it to the primary.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import is_connection_error
from core.telemetry import replica_reads

TOKEN_HEADER = "X-Consistency-Token"
TOKEN_PATTERN = r"^[0-9A-F]{1,8}/[0-9A-F]{1,8}$"

# Why a read went to the primary: the `reason` of `db.replica.reads`.
PrimaryReason = Literal["behind", "miss", "replica_unavailable", "conflict"]

# A replica cancels a query that holds rows its WAL replay must remove, once
# max_standby_streaming_delay has passed.
RECOVERY_CONFLICT = "40001"


async def write_position(session: AsyncSession) -> str:
    """The primary's WAL position now: after a commit, at or past that commit."""
    return await session.scalar(text("SELECT pg_current_wal_lsn()::text"))


async def replica_has(session: AsyncSession, token: str) -> bool:
    """A session on a primary (no replica configured) has every write."""
    # Sent as text: asyncpg would encode a pg_lsn parameter as an integer.
    return await session.scalar(
        text(
            "SELECT coalesce("
            "pg_last_wal_replay_lsn() >= CAST(CAST(:token AS text) AS pg_lsn), true)"
        ),
        {"token": token},
    )


class ReplicaReads[R]:
    """Runs one read on the replica's repository or the primary's, as above."""

    def __init__(
        self,
        *,
        replica_session: AsyncSession,
        replica: R,
        primary: R,
    ):
        self.replica_session = replica_session
        self.replica = replica
        self.primary = primary

    async def read[T](
        self, query: Callable[[R], Awaitable[T | None]], *, token: str | None
    ) -> T | None:
        try:
            if token is not None and not await replica_has(self.replica_session, token):
                return await self._from_primary(query, reason="behind")
            found = await query(self.replica)
        except (OSError, DBAPIError) as exc:
            reason = _fallback_reason(exc)
            if reason is None:
                raise
            return await self._from_primary(query, reason=reason)
        if found is None and token is None:
            return await self._from_primary(query, reason="miss")
        replica_reads.add(1, {"source": "replica"})
        return found

    async def _from_primary[T](
        self, query: Callable[[R], Awaitable[T | None]], *, reason: PrimaryReason
    ) -> T | None:
        replica_reads.add(1, {"source": "primary", "reason": reason})
        return await query(self.primary)


def _fallback_reason(exc: Exception) -> PrimaryReason | None:
    if is_connection_error(exc):
        return "replica_unavailable"
    if getattr(getattr(exc, "orig", None), "sqlstate", None) == RECOVERY_CONFLICT:
        return "conflict"
    return None
