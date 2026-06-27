"""add arb_opportunities (event-level structural arbitrage, PYT-141)

Detected dutch-book / ladder-spread arbitrage baskets per multi-option event, refreshed each run.
Legs are stored as JSONB (market_id, side, price); ev is the locked profit per unit basket.

Revision ID: e8b2c4d6f0a1
Revises: d7f3b9e1c5a2
Create Date: 2026-06-27 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "e8b2c4d6f0a1"
down_revision: str | Sequence[str] | None = "d7f3b9e1c5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arb_opportunities",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_slug", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ev", sa.Double(), nullable=False),
        sa.Column("legs", JSONB(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("arb_opportunities_event", "arb_opportunities", ["event_slug"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("arb_opportunities_event", table_name="arb_opportunities")
    op.drop_table("arb_opportunities")
