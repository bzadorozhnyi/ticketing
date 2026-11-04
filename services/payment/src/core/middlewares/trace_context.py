import re
import secrets

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from core.logging import trace_id_var

TRACEPARENT = re.compile(
    r"(?P<version>[0-9a-f]{2})"
    r"-(?P<trace_id>[0-9a-f]{32})"
    r"-(?P<parent_id>[0-9a-f]{16})"
    r"-[0-9a-f]{2}"
    r"(?P<extra>-.*)?"
)


def parse_trace_id(*, traceparent: str | None) -> str | None:
    """Returns the trace id of a valid W3C traceparent header, otherwise None.

    The header comes from the client, so only a strictly valid value may reach
    the logs. Only version 00 forbids extra fields: later versions may add them.
    """
    match = TRACEPARENT.fullmatch(traceparent or "")
    if match is None:
        return None

    is_invalid = (
        match["version"] == "ff"
        or (match["version"] == "00" and match["extra"] is not None)
        or set(match["trace_id"]) == {"0"}
        or set(match["parent_id"]) == {"0"}
    )
    return None if is_invalid else match["trace_id"]


class TraceContextMiddleware:
    """Sets the trace id without resetting it: uvicorn logs an unhandled error and
    its 500 outside every middleware, in the task it runs for this request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        traceparent = Headers(scope=scope).get("traceparent")
        trace_id = parse_trace_id(traceparent=traceparent) or secrets.token_hex(16)
        trace_id_var.set(trace_id)
        await self.app(scope, receive, send)
