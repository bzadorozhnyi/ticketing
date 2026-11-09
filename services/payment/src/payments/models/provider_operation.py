import uuid
from enum import StrEnum

from sqlalchemy import BigInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db import BaseModel


class ProviderOperationKind(StrEnum):
    CHARGE = "charge"
    REFUND = "refund"


class ProviderOperation(BaseModel):
    """The mock provider's own record, standing in for the state of an external system.
    It is committed apart from our transaction, so it survives a crash of ours, and it
    is the only place that says how much money was really taken.
    """

    __tablename__ = "provider_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=func.uuidv7()
    )
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str]
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str]
    status: Mapped[str]
