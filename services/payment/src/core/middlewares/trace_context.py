import secrets

from opentelemetry import trace
from starlette.types import ASGIApp, Receive, Scope, Send

from core.logging import trace_id_var
from core.telemetry import current_trace_id


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

        # FastAPI's telemetry runs first and continues a valid `traceparent` from the
        # gateway; a forged header never reaches the logs, only the parsed id does.
        span = trace.get_current_span()
        trace_id_var.set(current_trace_id(span) or secrets.token_hex(16))
        await self.app(scope, receive, send)
