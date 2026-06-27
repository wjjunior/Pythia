"""make paper_trades.estimate_id nullable (arb-basket legs, PYT-145)

An arb-basket leg is a market price play, not a model forecast, so it carries no estimate. Its fill
price comes from the market's latest snapshot directly; estimate_id is NULL.

Revision ID: f1a3c7e9b2d4
Revises: e8b2c4d6f0a1
Create Date: 2026-06-27 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a3c7e9b2d4"
down_revision: str | Sequence[str] | None = "e8b2c4d6f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "paper_trades",
        "estimate_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "paper_trades",
        "estimate_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
