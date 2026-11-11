"""add request fingerprint

The SQL must compute what `request_fingerprint()` in orders/use_cases/create_order.py
does.

Revision ID: b4e9f2a7c1d5
Revises: 8a1d4c6e2b7f
Create Date: 2026-11-11 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e9f2a7c1d5"
down_revision: str | Sequence[str] | None = "8a1d4c6e2b7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "orders", sa.Column("request_fingerprint", sa.String(), nullable=True)
    )
    op.execute(
        """
        UPDATE orders
        SET request_fingerprint = encode(
            sha256(convert_to(hold_id::text || ':' || payment_token, 'UTF8')), 'hex'
        )
        WHERE request_fingerprint IS NULL
        """
    )
    op.execute(
        """
        DELETE FROM order_payment_tokens t
        USING orders o
        WHERE o.id = t.order_id AND o.status IN ('confirmed', 'failed')
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "request_fingerprint")
