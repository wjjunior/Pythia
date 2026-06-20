"""reconcile postmortems/improvement_candidates with §4.7 (PYT-37)

Aligns the §5.5 tables to the §4.7 schema-name contract: postmortems.surprise -> brier, add
category + resolution_id, estimate_id nullable, index by (category, created_at);
improvement_candidates.action -> kind, index by (status, created_at). The typed adoption columns
(brier_per_dollar / n / human_approved / requires_review) are kept by design over the doc's
harness_delta jsonb + decided_by — a deliberate, documented deviation.

Revision ID: e7f2b9c4d1a8
Revises: d4e6a2c1f9b7
Create Date: 2026-06-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f2b9c4d1a8"
down_revision: str | Sequence[str] | None = "d4e6a2c1f9b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # postmortems
    op.alter_column("postmortems", "surprise", new_column_name="brier")
    op.add_column("postmortems", sa.Column("category", sa.Text(), nullable=False))
    op.add_column(
        "postmortems",
        sa.Column("resolution_id", sa.UUID(), sa.ForeignKey("resolutions.id"), nullable=True),
    )
    op.alter_column("postmortems", "estimate_id", existing_type=sa.UUID(), nullable=True)
    op.drop_index("postmortems_market", table_name="postmortems")
    op.create_index("postmortems_category", "postmortems", ["category", "created_at"], unique=False)

    # improvement_candidates
    op.alter_column("improvement_candidates", "action", new_column_name="kind")
    op.drop_index("improvement_candidates_status", table_name="improvement_candidates")
    op.create_index(
        "improvement_status", "improvement_candidates", ["status", "created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("improvement_status", table_name="improvement_candidates")
    op.create_index(
        "improvement_candidates_status", "improvement_candidates", ["status"], unique=False
    )
    op.alter_column("improvement_candidates", "kind", new_column_name="action")

    op.drop_index("postmortems_category", table_name="postmortems")
    op.create_index("postmortems_market", "postmortems", ["market_id"], unique=False)
    op.alter_column("postmortems", "estimate_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("postmortems", "resolution_id")
    op.drop_column("postmortems", "category")
    op.alter_column("postmortems", "brier", new_column_name="surprise")
