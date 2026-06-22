"""reconcile postmortems/improvement_candidates with §4.7 (PYT-37)

Aligns the §5.5 tables to the §4.7 schema-name contract: postmortems.surprise -> brier, add
category + resolution_id, estimate_id nullable, index by (category, created_at);
improvement_candidates.action -> kind, index by (status, created_at). The typed adoption columns
(brier_per_dollar / n / human_approved / requires_review) are kept by design over the doc's
harness_delta jsonb + decided_by — a deliberate, documented deviation.

The data steps below are no-ops on a fresh database (the post-mortem loop has no caller yet, so the
table is empty), but are written defensively so the migration is correct even if rows exist.

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
    # postmortems: surprise (confidence-weighted) -> brier (the raw triggering miss). Recompute the
    # value for any existing rows, since the old number is not (p_model - outcome)^2.
    op.alter_column("postmortems", "surprise", new_column_name="brier")
    op.execute(
        "UPDATE postmortems SET brier = "
        "(estimates.p_model - resolutions.outcome) * (estimates.p_model - resolutions.outcome) "
        "FROM estimates, resolutions "
        "WHERE postmortems.estimate_id = estimates.id "
        "AND postmortems.market_id = resolutions.market_id"
    )

    # category: add nullable, backfill from the parent market, then enforce NOT NULL.
    op.add_column("postmortems", sa.Column("category", sa.Text(), nullable=True))
    op.execute(
        "UPDATE postmortems SET category = markets.category "
        "FROM markets WHERE postmortems.market_id = markets.id"
    )
    op.alter_column("postmortems", "category", existing_type=sa.Text(), nullable=False)

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

    # estimate_id became nullable here; restoring NOT NULL is only safe if no NULLs were written.
    null_estimates = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM postmortems WHERE estimate_id IS NULL"))
        .scalar_one()
    )
    if null_estimates:
        raise RuntimeError(
            f"cannot downgrade past e7f2b9c4d1a8: {null_estimates} postmortems row(s) have a NULL "
            "estimate_id; backfill or remove them before downgrading"
        )
    op.alter_column("postmortems", "estimate_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("postmortems", "resolution_id")
    op.drop_column("postmortems", "category")
    op.alter_column("postmortems", "brier", new_column_name="surprise")
