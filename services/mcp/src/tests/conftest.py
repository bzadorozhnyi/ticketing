import os
import socket
from collections.abc import AsyncIterator

import pytest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# Settings are read on import: the fake services' address is set before app imports.
SERVICES_PORT = _free_port()
for name in ("CATALOG", "SEAT_HOLD", "BOOKING"):
    os.environ[f"UPSTREAMS__{name}_URL"] = f"http://127.0.0.1:{SERVICES_PORT}"

import httpx2  # noqa: E402
from aiohttp import web  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from main import create_app  # noqa: E402
from tests.helpers.services import FakeServices  # noqa: E402

USER = "user-1"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def services() -> AsyncIterator[FakeServices]:
    fake = FakeServices()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", SERVICES_PORT).start()
    yield fake
    await runner.cleanup()


class User:
    """What the user answers when a tool asks them to confirm a payment."""

    def __init__(self):
        self.answer = ElicitResult(action="accept", content={"payment_token": "tok_ok"})
        self.asked: list[str] = []

    async def __call__(self, context, params):
        self.asked.append(params.message)
        return self.answer


@pytest.fixture
def user() -> User:
    return User()


async def _connect(headers: dict[str, str], user: User) -> AsyncIterator[Client]:
    """Over HTTP to the real app, with the headers the gateway would send."""
    app = create_app()
    async with app.router.lifespan_context(app):
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers
        )
        transport = streamable_http_client("http://mcp:8000/mcp", http_client=http)
        async with Client(transport, elicitation_callback=user) as client:
            yield client


@pytest.fixture
async def mcp_client(services, user) -> AsyncIterator[Client]:
    async for client in _connect({"x-user-id": USER}, user):
        yield client


@pytest.fixture
async def anonymous_client(services, user) -> AsyncIterator[Client]:
    async for client in _connect({}, user):
        yield client
