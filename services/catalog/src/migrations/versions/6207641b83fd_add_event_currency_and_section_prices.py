"""add event currency and section prices

Revision ID: 6207641b83fd
Revises: b9f89158b374
Create Date: 2026-11-04 10:43:36.691782

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6207641b83fd"
down_revision: str | Sequence[str] | None = "b9f89158b374"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "events",
        sa.Column(
            "currency", sa.String(length=3), server_default="UAH", nullable=False
        ),
    )
    op.alter_column("events", "currency", server_default=None)
    op.create_unique_constraint(
        op.f("uq_events_id_venue_id"), "events", ["id", "venue_id"]
    )
    op.create_unique_constraint(
        op.f("uq_sections_id_venue_id"), "sections", ["id", "venue_id"]
    )
    op.create_table(
        "section_prices",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_minor >= 0", name=op.f("ck_section_prices_price_minor_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "venue_id"],
            ["events.id", "events.venue_id"],
            name=op.f("fk_section_prices_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["section_id", "venue_id"],
            ["sections.id", "sections.venue_id"],
            name=op.f("fk_section_prices_section_id_sections"),
        ),
        sa.PrimaryKeyConstraint(
            "event_id", "section_id", name=op.f("pk_section_prices")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("section_prices")
    op.drop_constraint(op.f("uq_sections_id_venue_id"), "sections", type_="unique")
    op.drop_constraint(op.f("uq_events_id_venue_id"), "events", type_="unique")
    op.drop_column("events", "currency")
