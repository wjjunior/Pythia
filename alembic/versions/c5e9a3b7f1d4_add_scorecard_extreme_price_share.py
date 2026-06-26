"""add trader_scorecards.extreme_price_share (scalper guard, PYT-116)

The fraction of a wallet's resolved entries at extreme prices (<= 0.10 or >= 0.90) — the
convergence-scalper tell. Persisted alongside skew/max_drawdown so the dashboard and audit can show
why a wallet was hard-capped to never-promotable (§4.2, AC-S5).

Revision ID: c5e9a3b7f1d4
Revises: b4d7e1f9a2c6
Create Date: 2026-06-26 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5e9a3b7f1d4"
down_revision: str | Sequence[str] | None = "b4d7e1f9a2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "trader_scorecards",
        sa.Column("extreme_price_share", sa.Double(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("trader_scorecards", "extreme_price_share")
