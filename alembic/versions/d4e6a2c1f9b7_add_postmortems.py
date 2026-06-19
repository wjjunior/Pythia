"""add postmortems + improvement_candidates (§4.7, §5.5, PYT-36)

Revision ID: d4e6a2c1f9b7
Revises: c3d5f1a2b8e4
Create Date: 2026-06-19 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e6a2c1f9b7"
down_revision: str | Sequence[str] | None = "c3d5f1a2b8e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "postmortems",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("estimate_id", sa.UUID(), nullable=False),
        sa.Column("failure_class", sa.Text(), nullable=False),
        sa.Column("surprise", sa.Double(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.ARRAY(sa.UUID()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["estimate_id"], ["estimates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("postmortems_market", "postmortems", ["market_id"], unique=False)

    op.create_table(
        "improvement_candidates",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("postmortem_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("requires_review", sa.Boolean(), nullable=False),
        sa.Column("brier_per_dollar", sa.Double(), nullable=True),
        sa.Column("n", sa.Integer(), nullable=True),
        sa.Column("human_approved", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["postmortem_id"], ["postmortems.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "improvement_candidates_status", "improvement_candidates", ["status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("improvement_candidates_status", table_name="improvement_candidates")
    op.drop_table("improvement_candidates")
    op.drop_index("postmortems_market", table_name="postmortems")
    op.drop_table("postmortems")
