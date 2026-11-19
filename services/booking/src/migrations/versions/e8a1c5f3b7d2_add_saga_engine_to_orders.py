"""add saga engine to orders

Revision ID: e8a1c5f3b7d2
Revises: d1f6a8c3e5b9
Create Date: 2026-11-19 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8a1c5f3b7d2"
down_revision: str | Sequence[str] | None = "d1f6a8c3e5b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A constant default is only metadata, and a check added NOT VALID and validated
    after reads the table without blocking writes (ADR 0008)."""
    op.add_column(
        "orders",
        sa.Column("saga_engine", sa.String(), server_default="builtin", nullable=False),
    )
    op.execute(
        "ALTER TABLE orders ADD CONSTRAINT ck_orders_saga_engine_known "
        "CHECK (saga_engine IN ('builtin', 'temporal')) NOT VALID"
    )
    op.execute("ALTER TABLE orders VALIDATE CONSTRAINT ck_orders_saga_engine_known")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_orders_saga_engine_known", "orders", type_="check")
    op.drop_column("orders", "saga_engine")
