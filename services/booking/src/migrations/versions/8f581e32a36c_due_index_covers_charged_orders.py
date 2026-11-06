"""due index covers charged orders

Revision ID: 8f581e32a36c
Revises: 8496bdd14e6d
Create Date: 2026-11-06 18:06:10.609780

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f581e32a36c"
down_revision: str | Sequence[str] | None = "8496bdd14e6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("ix_orders_saga_due", table_name="orders")
    op.create_index(
        "ix_orders_saga_due",
        "orders",
        ["next_attempt_at"],
        postgresql_where=sa.text("saga_step IN ('started', 'charged')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_orders_saga_due", table_name="orders")
    op.create_index(
        "ix_orders_saga_due",
        "orders",
        ["next_attempt_at"],
        postgresql_where=sa.text("saga_step = 'started'"),
    )
