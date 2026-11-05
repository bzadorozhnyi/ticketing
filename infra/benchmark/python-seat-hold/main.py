"""The Python baseline for the seat-hold benchmark: the same SQL and rules, in one file."""

import json
import os
import secrets
import sys
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

TTL_SECONDS = float(os.environ.get("HOLDS__TTL_SECONDS", "300"))
MAX_SEATS = 10
TAKE_SEATS = """
INSERT INTO holds (event_id, seat_id, hold_id, owner_id, expires_at)
SELECT $1, requested.seat_id, $2, $3, now() + make_interval(secs => $4)
FROM unnest($5::uuid[]) AS requested (seat_id)
ORDER BY requested.seat_id
ON CONFLICT (event_id, seat_id) DO UPDATE
SET hold_id = EXCLUDED.hold_id,
    owner_id = EXCLUDED.owner_id,
    expires_at = EXCLUDED.expires_at
WHERE holds.expires_at <= now()
RETURNING seat_id, expires_at
"""


class HoldIn(BaseModel):
    event_id: uuid.UUID
    seat_ids: list[uuid.UUID]


class SeatsTaken(Exception):
    def __init__(self, seat_ids: list[uuid.UUID]) -> None:
        self.seat_ids = seat_ids


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.pool = await asyncpg.create_pool(
        host=os.environ["DB__HOST"],
        port=int(os.environ.get("DB__PORT", "5432")),
        database=os.environ["DB__NAME"],
        user=os.environ["DB__USER"],
        password=os.environ["DB__PASS"],
        min_size=1,
        max_size=int(os.environ.get("POOL__MAX_CONNECTIONS", "10")),
    )
    yield
    await app.state.pool.close()


app = FastAPI(lifespan=lifespan)


def error(status: int, code: str, message: str, details: list[str]) -> JSONResponse:
    content = {"code": code, "message": message, "details": details}
    return JSONResponse(content, status_code=status)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error(422, "validation_error", str(exc.errors()[0]["msg"]), [])


def validate(seat_ids: list[uuid.UUID]) -> str | None:
    if not seat_ids:
        return "seat_ids is empty"
    if len(seat_ids) > MAX_SEATS:
        return f"seat_ids has more than {MAX_SEATS} seats"
    if len(set(seat_ids)) != len(seat_ids):
        return "seat_ids has duplicates"
    return None


async def hold_seats(
    pool: asyncpg.Pool, hold_id: uuid.UUID, body: HoldIn, owner_id: str
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn, conn.transaction():
        taken = await conn.fetch(
            TAKE_SEATS, body.event_id, hold_id, owner_id, TTL_SECONDS, body.seat_ids
        )
        if len(taken) != len(body.seat_ids):
            got = {row["seat_id"] for row in taken}
            raise SeatsTaken([s for s in body.seat_ids if s not in got])
    return taken


@app.post("/api/v1/holds", status_code=201, response_model=None)
async def create_hold(request: Request, body: HoldIn) -> dict | JSONResponse:
    owner_id = request.headers.get("x-user-id")
    if not owner_id:
        return error(401, "unauthorized", "Authentication required", [])
    if message := validate(body.seat_ids):
        return error(422, "validation_error", message, [])

    hold_id = uuid.uuid7()
    try:
        taken = await hold_seats(request.app.state.pool, hold_id, body, owner_id)
    except SeatsTaken as exc:
        details = [str(seat_id) for seat_id in exc.seat_ids]
        return error(409, "conflict", "Some seats are taken", details)
    return {
        "hold_id": str(hold_id),
        "event_id": str(body.event_id),
        "seat_ids": [str(seat_id) for seat_id in body.seat_ids],
        "expires_at": min(row["expires_at"] for row in taken).isoformat(),
    }


@app.delete("/api/v1/holds/{hold_id}", status_code=204, response_model=None)
async def release_hold(request: Request, hold_id: uuid.UUID) -> Response:
    owner_id = request.headers.get("x-user-id")
    if not owner_id:
        return error(401, "unauthorized", "Authentication required", [])
    await request.app.state.pool.execute(
        "DELETE FROM holds WHERE hold_id = $1 AND owner_id = $2", hold_id, owner_id
    )
    return Response(status_code=204)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(request: Request) -> dict[str, dict[str, str]]:
    await request.app.state.pool.fetchval("SELECT 1")
    return {"checks": {"db": "ok"}}


class LogRequests:
    """Pure ASGI on purpose: `@app.middleware("http")` halved the throughput and took
    the p99 of a spike from 0.6 s to 3 s, which made this baseline unfairly weak.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        started = time.perf_counter()
        trace_id = secrets.token_hex(16)
        status = 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        await self.inner(scope, receive, capture)
        line = {
            "level": "INFO",
            "message": "Request finished",
            "method": scope["method"],
            "path": scope["path"],
            "status": status,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "trace_id": trace_id,
        }
        sys.stdout.write(json.dumps(line) + "\n")


app.add_middleware(LogRequests)
