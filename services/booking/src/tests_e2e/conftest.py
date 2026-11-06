"""Real commits and many connections. Run it on its own: `pytest src/tests_e2e`."""

import asyncio
import os
from pathlib import Path

import pytest
from aiohttp import web
from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from tests.helpers.env import load_dotenv_into_environ

load_dotenv_into_environ(".env.test", "../.env.test")
os.environ["DB__NAME"] = "testing_booking_e2e_db"

from core.db import async_session, engine  # noqa: E402
from core.http import HttpClient  # noqa: E402
from core.settings import UpstreamSettings, settings  # noqa: E402
from main import app  # noqa: E402
from orders.models.order import Order  # noqa: E402
from outbox.models.message import OutboxMessage  # noqa: E402
from tests.helpers.db import create_db, drop_db  # noqa: E402
from tests_e2e.helpers import Upstreams  # noqa: E402

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
def asgi_app():
    return app


@pytest.fixture
async def empty_tables():
    async with async_session() as session:
        await session.execute(delete(Order))
        await session.execute(delete(OutboxMessage))
        await session.commit()


@pytest.fixture
async def upstreams():
    stub = Upstreams()
    runner = web.AppRunner(stub.application())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0][:2]
    stub.url = f"http://{host}:{port}"
    yield stub
    await runner.cleanup()


@pytest.fixture
async def http(upstreams):
    client = HttpClient(
        base_url=upstreams.url,
        settings=UpstreamSettings(CONNECT_TIMEOUT_SECONDS=0.5, TIMEOUT_SECONDS=0.2),
    )
    await client.connect()
    yield client
    await client.close()
