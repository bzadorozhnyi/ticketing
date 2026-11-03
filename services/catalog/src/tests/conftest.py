import asyncio
import pkgutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ALEMBIC_INI_PATH = Path(__file__).resolve().parent.parent / "alembic.ini"

# Must run before any project-internal import below — core.settings reads
# straight off os.environ, so whatever it sees has to already be correct by
# the time it is first imported.
from tests.helpers.env import load_dotenv_into_environ  # noqa: E402

load_dotenv_into_environ(".env.test", "../.env.test")

from core.db import get_session  # noqa: E402
from core.settings import settings  # noqa: E402
from main import app  # noqa: E402
from tests.helpers.db import create_db, drop_db  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def alembic_config():
    return Config(str(ALEMBIC_INI_PATH))


@pytest.fixture(scope="session")
async def async_engine(alembic_config):
    db_url = settings.DB.url
    await drop_db(db_url=db_url)
    await create_db(db_url=db_url)

    await asyncio.to_thread(command.upgrade, alembic_config, "head")

    engine = create_async_engine(db_url)

    yield engine

    await engine.dispose()
    await drop_db(db_url=db_url)


@pytest.fixture(scope="session")
def session_factory(async_engine):
    return async_sessionmaker(
        bind=async_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest.fixture
async def db_connection(async_engine):
    """One test = one connection-level transaction, rolled back at the end.
    Rows written here are invisible to any other connection, so tests that
    need several concurrent connections belong to the e2e suite.
    """
    async with async_engine.connect() as conn:
        transaction = await conn.begin()
        yield conn
        await transaction.rollback()


@pytest.fixture
async def db_session(db_connection, session_factory):
    """A session bound to an already-begun external transaction treats its
    own commit() as a savepoint release, not a real commit — so app code (and
    factories) can commit freely without breaking isolation between tests.
    """
    async with session_factory(bind=db_connection) as session:
        yield session


@pytest.fixture
async def async_client(db_session):
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client

    app.dependency_overrides.clear()


# Auto-load every fixture-factory in tests/factories/ without manual imports.
pytest_plugins = [
    f"tests.factories.{modname}"
    for _, modname, _ in pkgutil.iter_modules(
        [str(Path(__file__).resolve().parent / "factories")]
    )
]
