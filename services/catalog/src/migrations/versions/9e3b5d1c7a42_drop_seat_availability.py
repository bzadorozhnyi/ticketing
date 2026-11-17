"""drop seat availability

Revision ID: 9e3b5d1c7a42
Revises: 4c1e8a7b2d90
Create Date: 2026-11-17 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e3b5d1c7a42"
down_revision: str | Sequence[str] | None = "4c1e8a7b2d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table("seat_availability")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "seat_availability",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("seat_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=4), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('held', 'sold', 'free')",
            name=op.f("ck_seat_availability_status_known"),
        ),
        sa.PrimaryKeyConstraint(
            "event_id", "seat_id", name=op.f("pk_seat_availability")
        ),
    )
