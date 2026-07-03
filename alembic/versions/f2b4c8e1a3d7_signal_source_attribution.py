"""signal_source columns on estimates/scores/paper_trades + candidate_signals.estimate_id FK

Revision ID: f2b4c8e1a3d7
Revises: e6a8c0d2f4b6
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b4c8e1a3d7"
down_revision: str | Sequence[str] | None = "e6a8c0d2f4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIGNAL_SOURCE_CHECK = (
    "signal_source IS NULL OR signal_source IN ('radar','smartmoney','arb','manual','unknown')"
)


def upgrade() -> None:
    """Add signal_source columns and candidate_signals.estimate_id FK."""
    op.add_column("estimates", sa.Column("signal_source", sa.Text(), nullable=True))
    op.create_check_constraint("estimates_signal_source_check", "estimates", _SIGNAL_SOURCE_CHECK)

    op.add_column("scores", sa.Column("signal_source", sa.Text(), nullable=True))
    op.create_check_constraint("scores_signal_source_check", "scores", _SIGNAL_SOURCE_CHECK)

    op.add_column("paper_trades", sa.Column("signal_source", sa.Text(), nullable=True))
    op.create_check_constraint(
        "paper_trades_signal_source_check", "paper_trades", _SIGNAL_SOURCE_CHECK
    )

    op.add_column(
        "candidate_signals",
        sa.Column("estimate_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "candidate_signals_estimate_id_fkey",
        "candidate_signals",
        "estimates",
        ["estimate_id"],
        ["id"],
    )


def downgrade() -> None:
    """Remove signal_source columns and candidate_signals.estimate_id FK."""
    op.drop_constraint(
        "candidate_signals_estimate_id_fkey", "candidate_signals", type_="foreignkey"
    )
    op.drop_column("candidate_signals", "estimate_id")

    op.drop_constraint("paper_trades_signal_source_check", "paper_trades", type_="check")
    op.drop_column("paper_trades", "signal_source")

    op.drop_constraint("scores_signal_source_check", "scores", type_="check")
    op.drop_column("scores", "signal_source")

    op.drop_constraint("estimates_signal_source_check", "estimates", type_="check")
    op.drop_column("estimates", "signal_source")
