from typing import Annotated, Any

from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Elicit, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from core.settings import settings
from server import ToolContext, mcp, upstreams_of, user_of


class PaymentConfirmation(BaseModel):
    payment_token: str = Field(
        description=(
            f"Your payment token; {settings.SERVER.PAYMENT_TOKEN_HINT} is a test card"
        )
    )


class Order(BaseModel):
    order_id: str
    hold_id: str
    status: str
    failure_reason: str | None = None
    consistency_token: str | None = Field(
        default=None, description="Pass it to get_order to see this order at once"
    )


def ask_payment(hold_id: str) -> Elicit[PaymentConfirmation]:
    """The user, not the model, confirms a payment and gives the card."""
    return Elicit(
        f"Pay for hold {hold_id}? Enter your payment token to confirm.",
        PaymentConfirmation,
    )


def order_of(body: dict[str, Any], consistency_token: str | None = None) -> Order:
    return Order(
        order_id=body["id"],
        hold_id=body["hold_id"],
        status=body["status"],
        failure_reason=body.get("failure_reason"),
        consistency_token=consistency_token,
    )


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True))
async def create_order(
    ctx: ToolContext,
    hold_id: str,
    confirmation: Annotated[
        ElicitationResult[PaymentConfirmation], Resolve(ask_payment)
    ],
) -> Order:
    """Pay for a hold. The user is asked to confirm and to give their payment token;
    if they decline, nothing is paid. The order starts as `pending` and becomes
    `confirmed` or `failed` within seconds: check it with get_order. Safe to repeat for
    the same hold: a repeat returns the same order and pays once.
    """
    if not isinstance(confirmation, AcceptedElicitation):
        raise ToolError("The user did not confirm the payment. Nothing was paid.")
    status, body, consistency_token = await upstreams_of(ctx).booking.create_order(
        user_id=user_of(ctx),
        hold_id=hold_id,
        payment_token=confirmation.data.payment_token,
        idempotency_key=f"order:{hold_id}",
    )
    if status == 202:
        return order_of(body, consistency_token)
    raise ToolError(f"The order was refused: {body.get('message', status)}.")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
async def get_order(
    ctx: ToolContext, order_id: str, consistency_token: str | None = None
) -> Order:
    """An order of the user with its status: `pending`, `confirmed` (paid, seats
    sold) or `failed` (not paid; see failure_reason).
    """
    status, body = await upstreams_of(ctx).booking.get_order(
        user_id=user_of(ctx), order_id=order_id, consistency_token=consistency_token
    )
    if status == 200:
        return order_of(body)
    raise ToolError(f"No order {order_id} for this user.")
