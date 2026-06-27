"""add markets.neg_risk (mutually-exclusive event flag, PYT-137)

Polymarket negRisk marks a market as one option of a mutually-exclusive event set. Stored at ingest
so event-level grouping/arbitrage classifies the event type without re-deriving it at read time.

Revision ID: d7f3b9e1c5a2
Revises: b2d4f6a8c1e3
Create Date: 2026-06-27 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f3b9e1c5a2"
down_revision: str | Sequence[str] | None = "b2d4f6a8c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "markets",
        sa.Column("neg_risk", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("markets", "neg_risk")
