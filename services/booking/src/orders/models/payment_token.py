import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class OrderPaymentToken(BaseModel):
    """The payment token of an order, kept apart from the order: the saga needs it only
    until the charge, so it can go once the order is final. Moving it out of `orders`
    takes three releases (TIK-64, 66, 67); until then `orders.payment_token` has it too.
    """

    __tablename__ = "order_payment_tokens"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    payment_token: Mapped[str]
