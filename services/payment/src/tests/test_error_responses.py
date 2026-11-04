import socket

import pytest
from fastapi import status
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db import is_connection_error


@pytest.mark.anyio
async def test_db_outage_returns_503_in_error_format(
    real_session_client, probe_routes, monkeypatch
):
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    monkeypatch.setattr("core.db.async_session", async_sessionmaker(engine))

    response = await real_session_client.get("/_probe/db")
    await engine.dispose()

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "code": "service_unavailable",
        "message": "Database is unavailable",
        "details": [],
    }


@pytest.mark.anyio
async def test_sql_error_stays_500(real_session_client, probe_routes):
    response = await real_session_client.get("/_probe/bad-sql")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json()["code"] == "internal_error"


@pytest.mark.anyio
async def test_unexpected_error_returns_500_without_leaking_details(
    real_session_client, probe_routes
):
    response = await real_session_client.get("/_probe/error")
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.json() == {
        "code": "internal_error",
        "message": "Internal server error",
        "details": [],
    }


@pytest.mark.anyio
async def test_validation_error_uses_error_format(async_client, probe_routes):
    response = await async_client.get("/_probe/validated", params={"limit": 0})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"][0]["loc"] == ["query", "limit"]


@pytest.mark.anyio
async def test_unknown_route_uses_error_format(async_client):
    response = await async_client.get("/no-such-route")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {
        "code": "not_found",
        "message": "Not Found",
        "details": [],
    }


@pytest.mark.anyio
async def test_wrong_method_uses_error_format_and_keeps_allow_header(async_client):
    response = await async_client.post("/health")
    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert response.json()["code"] == "http_error"
    assert response.headers["allow"] == "GET"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConnectionRefusedError(), True),
        (socket.gaierror(), True),
        (TimeoutError(), True),
        (DBAPIError("SELECT 1", {}, Exception(), connection_invalidated=True), True),
        (DBAPIError("SELECT 1", {}, Exception(), connection_invalidated=False), False),
        (ValueError(), False),
    ],
    ids=["refused", "dns", "timeout", "stale", "sql-error", "other"],
)
def test_is_connection_error(error, expected):
    assert is_connection_error(error) is expected
