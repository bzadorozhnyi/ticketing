"""drop payment token column

Revision ID: d1f6a8c3e5b9
Revises: c9d3e5f7a2b4
Create Date: 2026-11-12 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1f6a8c3e5b9"
down_revision: str | Sequence[str] | None = "c9d3e5f7a2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("orders", "payment_token")


def downgrade() -> None:
    """Downgrade schema. The tokens are gone; the column comes back empty."""
    op.add_column("orders", sa.Column("payment_token", sa.String(), nullable=True))
