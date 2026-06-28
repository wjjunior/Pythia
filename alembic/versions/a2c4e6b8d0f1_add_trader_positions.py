"""trader_positions: a tracked wallet's current data-api holdings, replaced per poll (PYT-157)

Revision ID: a2c4e6b8d0f1
Revises: f1a3c7e9b2d4
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a2c4e6b8d0f1"
down_revision: str | Sequence[str] | None = "f1a3c7e9b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_PK_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "trader_positions",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column("trader_id", _UUID, sa.ForeignKey("tracked_traders.id"), nullable=False),
        sa.Column("condition_id", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("size", sa.Double(), nullable=False),
        sa.Column("avg_price", sa.Double(), nullable=False),
        sa.Column("cur_price", sa.Double(), nullable=False),
        sa.Column("current_value", sa.Double(), nullable=False),
        sa.Column("cash_pnl", sa.Double(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True)),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "trader_id", "condition_id", "outcome", name="trader_positions_trader_market_outcome"
        ),
    )
    op.create_index("trader_positions_trader", "trader_positions", ["trader_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("trader_positions_trader", table_name="trader_positions")
    op.drop_table("trader_positions")
