from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from starlette.requests import Request

from clients.booking import BookingClient
from clients.catalog import CatalogClient
from clients.seat_hold import SeatHoldClient
from core.http import HttpClient
from core.settings import settings

INSTRUCTIONS = """\
Tools to find events and buy tickets for the signed-in user. A purchase is: find an
event (search_events), choose a section (get_event_sections), choose seats from its map
(get_section_seats), hold them (hold_seats), then pay (create_order) and check the
order (get_order). The user confirms every payment themselves; never invent a payment
token.

A user may hold at most 10 seats of one event. Asked for more, hold none: tell the user
the limit and ask whether 10 or fewer will do.
"""


class Upstreams:
    """The services the tools call, connected once per process."""

    def __init__(self) -> None:
        upstreams = settings.UPSTREAMS
        self.http = [
            HttpClient(base_url=upstreams.CATALOG_URL, settings=upstreams),
            HttpClient(base_url=upstreams.SEAT_HOLD_URL, settings=upstreams),
            HttpClient(base_url=upstreams.BOOKING_URL, settings=upstreams),
        ]
        self.catalog = CatalogClient(http=self.http[0])
        self.seat_hold = SeatHoldClient(http=self.http[1])
        self.booking = BookingClient(http=self.http[2])


@asynccontextmanager
async def lifespan(_: MCPServer) -> AsyncGenerator[Upstreams]:
    upstreams = Upstreams()
    for http in upstreams.http:
        await http.connect()
    try:
        yield upstreams
    finally:
        for http in upstreams.http:
            await http.close()


def gateway_user(ctx: ServerRequestContext[Any, Any]) -> str | None:
    """The user a request state belongs to, so one user's state fails for another."""
    headers = getattr(ctx.request, "headers", None)
    return None if headers is None else headers.get("x-user-id")


def request_state_security() -> RequestStateSecurity:
    """Shared keys from the settings. The SDK's default is a key made by each process
    at start: a retry that reached another pod could not open the state.
    """
    return RequestStateSecurity(
        keys=settings.SERVER.REQUEST_STATE_KEYS, bind_principal=gateway_user
    )


mcp = MCPServer(
    "ticketing",
    instructions=INSTRUCTIONS,
    lifespan=lifespan,
    request_state_security=request_state_security(),
)

# What a tool receives besides the model's arguments: the clients made by the lifespan
# and the HTTP request the gateway sent. A plain alias, not a `type` statement: the SDK
# does not see through the latter and would offer `ctx` to the model as an argument.
ToolContext = Context[Upstreams, Request]


def upstreams_of(ctx: ToolContext) -> Upstreams:
    return ctx.request_context.lifespan_context


def user_of(ctx: ToolContext) -> str:
    """The user the gateway authenticated: it sets X-User-Id from the token and drops
    the token, so this server never sees one.
    """
    user_id = (ctx.headers or {}).get("x-user-id")
    if not user_id:
        raise ToolError(
            "Not signed in: call this server through the gateway with a token."
        )
    return user_id
