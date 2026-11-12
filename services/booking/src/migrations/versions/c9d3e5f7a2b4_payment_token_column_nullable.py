"""payment token column nullable

The DDL comes last: its lock is held until the commit.

Revision ID: c9d3e5f7a2b4
Revises: b4e9f2a7c1d5
Create Date: 2026-11-12 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d3e5f7a2b4"
down_revision: str | Sequence[str] | None = "b4e9f2a7c1d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
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
    op.alter_column("orders", "payment_token", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Downgrade schema. The column stays nullable: newer orders have no token in it."""
