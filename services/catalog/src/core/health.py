from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import text

from core.db import SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "A dependency is down"}
    },
)
async def ready(session: SessionDep) -> JSONResponse:
    checks = {"db": "ok"}
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.bind(error=repr(exc)).warning("Readiness check failed: db unreachable")
        checks["db"] = "unreachable"

    status_code = (
        status.HTTP_200_OK
        if all(value == "ok" for value in checks.values())
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=status_code, content={"checks": checks})
