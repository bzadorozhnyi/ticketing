"""backfill order payment tokens

The migrate step of moving payment tokens out of orders (TIK-66). Only orders still in
flight need their token; a final order never charges again. They are few, so one
statement does it; ON CONFLICT keeps it safe to repeat and safe next to the release
before, which writes token rows itself. No process older than TIK-64 may still run:
those create orders without a token row.

Revision ID: 8a1d4c6e2b7f
Revises: 5d78bac08591
Create Date: 2026-11-11 19:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8a1d4c6e2b7f"
down_revision: str | Sequence[str] | None = "5d78bac08591"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        INSERT INTO order_payment_tokens (order_id, payment_token)
        SELECT id, payment_token FROM orders WHERE status = 'pending'
        ON CONFLICT (order_id) DO NOTHING
        """
    )


def downgrade() -> None:
    """The copied rows stay: the release before reads them first anyway."""
