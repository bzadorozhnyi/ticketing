from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from server import ToolContext, mcp, upstreams_of, user_of


class Hold(BaseModel):
    hold_id: str
    event_id: str
    seat_ids: list[str]
    expires_at: str


class Released(BaseModel):
    hold_id: str
    released: bool


@mcp.tool()
async def hold_seats(ctx: ToolContext, event_id: str, seat_ids: list[str]) -> Hold:
    """Hold seats for the user for 5 minutes, so nobody else can take them while
    the user pays. All or none: if any seat is taken, nothing is held and the error
    names the taken seats; choose others from get_section_seats. At most 10 seats per
    request and per user for one event.
    """
    status, body = await upstreams_of(ctx).seat_hold.hold(
        user_id=user_of(ctx), event_id=event_id, seat_ids=seat_ids
    )
    if status == 201:
        return Hold(**body)
    if status == 409 and body.get("code") == "already_held_by_you":
        raise ToolError(
            f"The user already holds these seats, in hold {', '.join(body['details'])}"
            " (an earlier answer may have been lost). Use that hold_id with"
            " create_order or release_hold; do not hold them again."
        )
    if status == 409 and body.get("code") == "hold_limit_reached":
        raise ToolError(f"{body['message']}. Release a hold with release_hold first.")
    if status == 409:
        raise ToolError(
            f"These seats are taken: {', '.join(body['details'])}. Nothing was held; "
            "choose other seats."
        )
    raise ToolError(f"The hold was refused: {body.get('message', status)}.")


@mcp.tool(annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True))
async def release_hold(ctx: ToolContext, hold_id: str) -> Released:
    """Give the seats of a hold back, for example when the user changes their mind.
    Safe to repeat; a hold that is already gone counts as released. A sold seat stays
    sold.
    """
    await upstreams_of(ctx).seat_hold.release(user_id=user_of(ctx), hold_id=hold_id)
    return Released(hold_id=hold_id, released=True)
