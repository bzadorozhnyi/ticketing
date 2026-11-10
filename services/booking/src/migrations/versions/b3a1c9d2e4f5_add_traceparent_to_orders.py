"""add traceparent to orders

Revision ID: b3a1c9d2e4f5
Revises: 55e105580c69
Create Date: 2026-11-10 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3a1c9d2e4f5"
down_revision: str | Sequence[str] | None = "55e105580c69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("orders", sa.Column("traceparent", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("orders", "traceparent")
