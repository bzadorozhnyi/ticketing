import secrets

from opentelemetry import trace
from starlette.types import ASGIApp, Receive, Scope, Send

from core.logging import trace_id_var


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

        trace_id_var.set(current_trace_id())
        await self.app(scope, receive, send)


def current_trace_id() -> str:
    """The trace of the current span. OpenTelemetry's middleware runs first and
    continues a valid `traceparent` from the gateway, or starts a new trace; a forged
    header never reaches the logs, because only the parsed id does.
    """
    context = trace.get_current_span().get_span_context()
    return (
        format(context.trace_id, "032x") if context.is_valid else secrets.token_hex(16)
    )
