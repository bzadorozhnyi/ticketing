"""create charges and refunds

Revision ID: 47f14ffba6a3
Revises:
Create Date: 2026-11-06 11:07:30.586197

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "47f14ffba6a3"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "charges",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'declined', 'cancelled')",
            name=op.f("ck_charges_status_known"),
        ),
        sa.CheckConstraint(
            "amount_minor >= 0", name=op.f("ck_charges_amount_minor_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_charges")),
        sa.UniqueConstraint("order_id", name=op.f("uq_charges_order_id")),
    )
    op.create_table(
        "refunds",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("charge_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["charge_id"], ["charges.id"], name=op.f("fk_refunds_charge_id_charges")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
        sa.UniqueConstraint("charge_id", name=op.f("uq_refunds_charge_id")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("refunds")
    op.drop_table("charges")
