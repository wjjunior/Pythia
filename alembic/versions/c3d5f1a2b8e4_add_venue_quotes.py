"""add venue_quotes (§4.7, §4.10, PYT-34)

Revision ID: c3d5f1a2b8e4
Revises: c3d8f1a920b4
Create Date: 2026-06-19 15:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d5f1a2b8e4"
down_revision: str | Sequence[str] | None = "c3d8f1a920b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "venue_quotes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("venue", sa.Text(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yes_price", sa.Double(), nullable=False),
        sa.Column("match_confidence", sa.Double(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("venue_quotes_market_time", "venue_quotes", ["market_id", "ts"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("venue_quotes_market_time", table_name="venue_quotes")
    op.drop_table("venue_quotes")
