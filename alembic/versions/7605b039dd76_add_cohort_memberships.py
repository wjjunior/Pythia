"""add cohort_memberships

Revision ID: 7605b039dd76
Revises: d9f1a3c5b7e2
Create Date: 2026-06-25 21:58:03.256942

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7605b039dd76"
down_revision: str | Sequence[str] | None = "d9f1a3c5b7e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_PK_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cohort_memberships",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column("paper_trade_id", _UUID, sa.ForeignKey("paper_trades.id"), nullable=False),
        sa.Column("cohort", sa.Text(), nullable=False),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("paper_trade_id", "cohort", name="cohort_memberships_uq"),
    )
    op.create_index("cohort_memberships_cohort", "cohort_memberships", ["cohort"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("cohort_memberships_cohort", table_name="cohort_memberships")
    op.drop_table("cohort_memberships")
