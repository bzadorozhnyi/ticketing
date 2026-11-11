"""Migrations that cannot stall the service, see ADR 0008.

A migration waiting for a lock holds a place in the lock queue, and every later query
on that table waits behind it, reads included. So a migration gives up a lock after
`LOCK_TIMEOUT_SECONDS` and runs again after a pause, and the service waits at most that
long. There is no statement_timeout: building an index or filling a column may take
minutes.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from sqlalchemy.exc import DBAPIError

from core.settings import MigrationSettings

LOCK_NOT_AVAILABLE = "55P03"


def connect_args(settings: MigrationSettings) -> dict[str, Any]:
    milliseconds = round(settings.LOCK_TIMEOUT_SECONDS * 1000)
    return {"server_settings": {"lock_timeout": f"{milliseconds}ms"}}


async def run_with_lock_retries(
    attempt: Callable[[], Awaitable[None]], *, settings: MigrationSettings
) -> None:
    """Each attempt is one transaction, so a failed one leaves nothing behind. Gives up
    after `ATTEMPTS`: a deploy that stops is better than one that stalls the service."""
    for number in range(1, settings.ATTEMPTS + 1):
        try:
            await attempt()
            return
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != LOCK_NOT_AVAILABLE:
                raise
            if number == settings.ATTEMPTS:
                raise
            delay = min(
                settings.RETRY_BASE_DELAY_SECONDS * 2 ** (number - 1),
                settings.MAX_RETRY_DELAY_SECONDS,
            )
            logger.bind(attempt=number, delay=delay).warning(
                "Migration could not take a lock, will try again"
            )
            await asyncio.sleep(delay)
