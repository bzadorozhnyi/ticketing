"""due index covers waiting orders

Revision ID: 55e105580c69
Revises: 8f581e32a36c
Create Date: 2026-11-06 19:08:38.371369

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "55e105580c69"
down_revision: str | Sequence[str] | None = "8f581e32a36c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("ix_orders_saga_due", table_name="orders")
    op.create_index(
        "ix_orders_saga_due",
        "orders",
        ["next_attempt_at"],
        postgresql_where=sa.text(
            "saga_step IN ('started', 'awaiting_charge', 'charged', 'awaiting_refund')"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_orders_saga_due", table_name="orders")
    op.create_index(
        "ix_orders_saga_due",
        "orders",
        ["next_attempt_at"],
        postgresql_where=sa.text("saga_step IN ('started', 'charged')"),
    )
