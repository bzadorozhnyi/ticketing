"""outbox created_at is the time of the insert

Revision ID: c7e2f4a1b9d3
Revises: b3a1c9d2e4f5
Create Date: 2026-11-11 12:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2f4a1b9d3"
down_revision: str | Sequence[str] | None = "b3a1c9d2e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("outbox", "created_at", server_default=sa.text("clock_timestamp()"))


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("outbox", "created_at", server_default=sa.text("now()"))
