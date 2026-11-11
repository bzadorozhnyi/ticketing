import asyncio
import subprocess
import sys
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from core.migrations import LOCK_NOT_AVAILABLE, connect_args, run_with_lock_retries
from core.settings import MigrationSettings, settings
from tests.conftest import ALEMBIC_INI_PATH


@pytest.mark.anyio
async def test_models_match_migrations(async_engine):
    """Runs in a fresh interpreter: this process has already imported every
    model through the app, which would hide a module missing from models_registry.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI_PATH), "check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


FAST = MigrationSettings(
    LOCK_TIMEOUT_SECONDS=0.2,
    ATTEMPTS=4,
    RETRY_BASE_DELAY_SECONDS=0.05,
    MAX_RETRY_DELAY_SECONDS=0.1,
)


@pytest.fixture
async def probe_table(async_engine):
    """A table of its own, committed, so other connections see and lock it."""
    async with async_engine.begin() as connection:
        await connection.execute(text("CREATE TABLE lock_probe (id int)"))
    yield
    async with async_engine.begin() as connection:
        await connection.execute(text("DROP TABLE lock_probe"))


@pytest.fixture
async def reader(async_engine, probe_table):
    """A long report: an open transaction that has read the table holds ACCESS SHARE."""
    connection = await async_engine.connect()
    await connection.begin()
    await connection.execute(text("SELECT count(*) FROM lock_probe"))
    yield connection
    await connection.close()


@pytest.fixture
async def migration_engine():
    engine = create_async_engine(settings.DB.url, connect_args=connect_args(FAST))
    yield engine
    await engine.dispose()


def add_column(engine, attempts: list[float]):
    async def attempt() -> None:
        attempts.append(time.monotonic())
        async with engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE lock_probe ADD COLUMN note text")
            )

    return attempt


@pytest.mark.anyio
async def test_a_migration_behind_a_long_reader_steps_aside_and_tries_again(
    reader, migration_engine
):
    attempts: list[float] = []

    async def end_the_report() -> None:
        await asyncio.sleep(0.5)
        await reader.rollback()

    report = asyncio.create_task(end_the_report())
    await run_with_lock_retries(add_column(migration_engine, attempts), settings=FAST)
    await report

    assert len(attempts) >= 2
    async with migration_engine.connect() as connection:
        columns = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'lock_probe' AND column_name = 'note'"
            )
        )
        assert columns.scalar_one() == "note"


@pytest.mark.anyio
async def test_a_lock_that_never_ends_fails_the_migration_after_its_attempts(
    reader, migration_engine
):
    attempts: list[float] = []
    started = time.monotonic()

    # Bounded: without the lock timeout the migration would wait for ever.
    with pytest.raises(DBAPIError) as error:
        async with asyncio.timeout(10):
            await run_with_lock_retries(
                add_column(migration_engine, attempts), settings=FAST
            )

    assert getattr(error.value.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE
    assert len(attempts) == FAST.ATTEMPTS
    # Each attempt waits only the lock timeout: the queue behind it never lasts longer.
    assert time.monotonic() - started < FAST.ATTEMPTS * (0.2 + 0.1) + 1


@pytest.mark.anyio
async def test_other_errors_are_not_retried(migration_engine, probe_table):
    attempts: list[float] = []

    async def broken() -> None:
        attempts.append(time.monotonic())
        async with migration_engine.begin() as connection:
            await connection.execute(text("ALTER TABLE lock_probe ADD COLUMN id int"))

    with pytest.raises(DBAPIError):
        await run_with_lock_retries(broken, settings=FAST)

    assert len(attempts) == 1
