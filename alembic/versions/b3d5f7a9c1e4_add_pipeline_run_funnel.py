"""pipeline_run_funnel: per-tick pipeline stage funnel diagnostic (PYT-164)

Revision ID: b3d5f7a9c1e4
Revises: a2c4e6b8d0f1
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b3d5f7a9c1e4"
down_revision: str | Sequence[str] | None = "a2c4e6b8d0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_PK_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pipeline_run_funnel",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("crawled", sa.Integer(), nullable=False),
        sa.Column("tracked_open", sa.Integer(), nullable=False),
        sa.Column("shortlisted", sa.Integer(), nullable=False),
        sa.Column("estimated", sa.Integer(), nullable=False),
        sa.Column("scored", sa.Integer(), nullable=False),
        sa.Column("radar_surfaced", sa.Integer(), nullable=False),
        sa.Column("dropped_window", sa.Integer(), nullable=False),
        sa.Column("dropped_win_multiple", sa.Integer(), nullable=False),
        sa.Column("dropped_win_probability", sa.Integer(), nullable=False),
        sa.Column("dropped_extreme_disagreement", sa.Integer(), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False),
    )
    op.create_index("pipeline_run_funnel_ts", "pipeline_run_funnel", ["ts"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("pipeline_run_funnel_ts", table_name="pipeline_run_funnel")
    op.drop_table("pipeline_run_funnel")
