import pytest
from fastapi import status
from loguru import logger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.logging import trace_id_var
from core.middlewares.trace_context import parse_trace_id

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_ID = "00f067aa0ba902b7"
VALID = f"00-{TRACE_ID}-{PARENT_ID}-01"


@pytest.fixture
def log_extras():
    extras = []
    sink_id = logger.add(lambda message: extras.append(message.record["extra"]))
    yield extras
    logger.remove(sink_id)


@pytest.fixture
def broken_db(monkeypatch):
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    monkeypatch.setattr("core.db.async_session", async_sessionmaker(engine))
    return engine


@pytest.mark.parametrize(
    "traceparent",
    [VALID, f"00-{TRACE_ID}-{PARENT_ID}-00", f"01-{TRACE_ID}-{PARENT_ID}-01-extra"],
    ids=["sampled", "not-sampled", "future-version-with-extra-field"],
)
def test_parse_trace_id_accepts_valid_header(traceparent):
    assert parse_trace_id(traceparent=traceparent) == TRACE_ID


@pytest.mark.parametrize(
    "traceparent",
    [
        None,
        "",
        "garbage",
        VALID.upper(),
        VALID + "\n",
        VALID + '\n{"message": "forged log line"}',
        VALID + "-extra",
        f"ff-{TRACE_ID}-{PARENT_ID}-01",
        f"00-{'0' * 32}-{PARENT_ID}-01",
        f"00-{TRACE_ID}-{'0' * 16}-01",
        f"00-{TRACE_ID[:-1]}-{PARENT_ID}-01",
        f"00-{TRACE_ID}-{PARENT_ID}",
    ],
    ids=[
        "missing",
        "empty",
        "garbage",
        "uppercase",
        "trailing-newline",
        "log-injection",
        "version-00-with-extra-field",
        "forbidden-version",
        "zero-trace-id",
        "zero-parent-id",
        "short-trace-id",
        "no-flags",
    ],
)
def test_parse_trace_id_rejects_invalid_header(traceparent):
    assert parse_trace_id(traceparent=traceparent) is None


@pytest.mark.anyio
async def test_valid_traceparent_keeps_trace_id_in_logs(
    real_session_client, probe_routes, broken_db, log_extras
):
    response = await real_session_client.get(
        "/_probe/db", headers={"traceparent": VALID}
    )
    await broken_db.dispose()

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert [extra["trace_id"] for extra in log_extras] == [TRACE_ID]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [{}, {"traceparent": "garbage"}],
    ids=["missing", "invalid"],
)
async def test_each_request_without_valid_traceparent_gets_new_trace_id(
    real_session_client, probe_routes, broken_db, log_extras, headers
):
    for _ in range(2):
        await real_session_client.get("/_probe/db", headers=headers)
    await broken_db.dispose()

    first, second = (extra["trace_id"] for extra in log_extras)
    assert first != second
    assert all(len(trace_id) == 32 for trace_id in (first, second))


@pytest.mark.anyio
async def test_trace_id_outlives_unhandled_error(real_session_client, probe_routes):
    response = await real_session_client.get(
        "/_probe/error", headers={"traceparent": VALID}
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert trace_id_var.get() == TRACE_ID
