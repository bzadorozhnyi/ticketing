"""Real commits and many connections. Run it on its own: `pytest src/tests_e2e`."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tests.helpers.env import load_dotenv_into_environ

load_dotenv_into_environ(".env.test", "../.env.test")
os.environ["DB__NAME"] = "testing_payment_e2e_db"

from core.db import async_session, engine  # noqa: E402
from core.settings import settings  # noqa: E402
from tests.helpers.db import create_db, drop_db  # noqa: E402

ALEMBIC_INI_PATH = Path(__file__).resolve().parent.parent / "alembic.ini"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
async def database():
    await drop_db(db_url=settings.DB.url)
    await create_db(db_url=settings.DB.url)
    config = Config(str(ALEMBIC_INI_PATH))
    await asyncio.to_thread(command.upgrade, config, "head")

    yield

    await engine.dispose()
    await drop_db(db_url=settings.DB.url)


@pytest.fixture
def session_maker():
    return async_session
