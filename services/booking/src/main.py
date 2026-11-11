import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from core import replication
from core.exceptions import AppException
from core.health import router as health_router
from core.logging import configure_logging
from core.middlewares.exception_handler import (
    app_exception_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from core.middlewares.trace_context import TraceContextMiddleware
from core.settings import settings
from core.telemetry import setup as setup_telemetry
from routes import router

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    """Background job, only with a replica: the probe in core/replication.py."""
    if settings.DB_REPLICA is None:
        yield
        return
    stop = asyncio.Event()
    probe = asyncio.create_task(
        replication.run(
            interval=settings.REPLICATION.PROBE_INTERVAL_SECONDS,
            slot=settings.REPLICATION.SLOT,
            stop=stop,
        )
    )
    yield
    stop.set()
    await probe


# No spans per dependency or serialization: instrumentation lives at the edges.
app = FastAPI(title="booking", telemetry={"operation_spans": False}, lifespan=lifespan)

app.add_middleware(TraceContextMiddleware)
app.include_router(router)
app.include_router(health_router)
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
setup_telemetry()
