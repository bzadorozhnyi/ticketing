"""The Python baseline for the gateway benchmark: the same request path, in one file."""

import json
import os
import secrets
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import unquote

import aiohttp
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

CATALOG_URL = os.environ["UPSTREAMS__CATALOG_URL"].rstrip("/")
JWT_SECRET = os.environ["AUTH__JWT_SECRET"]
MAX_BODY_BYTES = 1024 * 1024
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
DROPPED_REQUEST_HEADERS = HOP_BY_HOP | {
    "authorization",
    "traceparent",
    "tracestate",
    "x-user-id",
}
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    connector = aiohttp.TCPConnector(limit=0, keepalive_timeout=3)
    timeout = aiohttp.ClientTimeout(total=10, connect=2)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        auto_decompress=False,
        skip_auto_headers={"User-Agent", "Accept-Encoding", "Content-Type"},
    ) as session:
        app.state.session = session
        yield


app = FastAPI(lifespan=lifespan)


def error(status: int, code: str, message: str, **headers: str) -> JSONResponse:
    content = {"code": code, "message": message, "details": []}
    return JSONResponse(content, status_code=status, headers=headers)


def log_request(trace_id: str, request: Request, status: int, started: float) -> None:
    line = {
        "level": "INFO",
        "message": "Request finished",
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "duration_ms": (time.perf_counter() - started) * 1000,
        "trace_id": trace_id,
    }
    sys.stdout.write(json.dumps(line) + "\n")


def authenticate(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    try:
        claims = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        return None
    return claims["sub"]


async def read_body(request: Request) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def upstream_headers(request: Request, trace_id: str, user_id: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name not in DROPPED_REQUEST_HEADERS
    }
    headers["traceparent"] = f"00-{trace_id}-{secrets.token_hex(8)}-01"
    headers["x-user-id"] = user_id
    return headers


async def stream(upstream: aiohttp.ClientResponse) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream.content.iter_any():
            yield chunk
    finally:
        upstream.release()


async def call_upstream(
    request: Request, trace_id: str, user_id: str, body: bytes
) -> Response:
    raw_path = request.scope["raw_path"].decode()
    query = request.url.query
    url = f"{CATALOG_URL}{raw_path}" + (f"?{query}" if query else "")
    try:
        upstream = await request.app.state.session.request(
            request.method,
            url,
            headers=upstream_headers(request, trace_id, user_id),
            data=body or None,
            allow_redirects=False,
        )
    except aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError:
        return error(503, "service_unavailable", "Upstream is unavailable")
    except TimeoutError:
        return error(504, "gateway_timeout", "Upstream did not respond in time")
    except aiohttp.ClientError:
        return error(502, "bad_gateway", "Upstream request failed")

    headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in HOP_BY_HOP
    }
    return StreamingResponse(
        stream(upstream), status_code=upstream.status, headers=headers
    )


async def handle(request: Request, trace_id: str) -> Response:
    user_id = authenticate(request)
    if user_id is None:
        return error(
            401,
            "unauthorized",
            "Authentication required",
            **{"WWW-Authenticate": "Bearer"},
        )
    if ".." in unquote(request.scope["raw_path"].decode()):
        return error(404, "not_found", "Not found")
    body = await read_body(request)
    if body is None:
        return error(413, "payload_too_large", "Request body is too large")
    return await call_upstream(request, trace_id, user_id, body)


@app.api_route("/api/v1/events", methods=METHODS)
@app.api_route("/api/v1/events/{rest:path}", methods=METHODS)
async def forward(request: Request) -> Response:
    started = time.perf_counter()
    trace_id = secrets.token_hex(16)
    response = await handle(request, trace_id)
    log_request(trace_id, request, response.status_code, started)
    return response


@app.get("/health")
@app.get("/ready")
async def health() -> dict[str, str]:
    return {"status": "ok"}
