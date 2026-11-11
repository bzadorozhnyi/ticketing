"""create order payment tokens

Revision ID: 5d78bac08591
Revises: c7e2f4a1b9d3
Create Date: 2026-11-11 18:13:45.449630

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d78bac08591"
down_revision: str | Sequence[str] | None = "c7e2f4a1b9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "order_payment_tokens",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("payment_token", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_payment_tokens_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_order_payment_tokens")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("order_payment_tokens")
