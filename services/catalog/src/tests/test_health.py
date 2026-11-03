import pytest
from fastapi import status
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db import get_session
from main import app


@pytest.mark.anyio
async def test_health_returns_ok(async_client):
    response = await async_client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_ready_returns_200_when_db_reachable(async_client):
    response = await async_client.get("/ready")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"checks": {"db": "ok"}}


@pytest.mark.anyio
async def test_ready_returns_503_when_db_unreachable(async_client):
    engine = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/none")
    unreachable_session = async_sessionmaker(engine)

    async def override_get_session():
        async with unreachable_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        response = await async_client.get("/ready")
    finally:
        await engine.dispose()

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {"checks": {"db": "unreachable"}}
