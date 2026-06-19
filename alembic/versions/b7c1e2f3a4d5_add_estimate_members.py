"""add estimate_members (§4.7, PYT-32)

Revision ID: b7c1e2f3a4d5
Revises: 4f92e7ece0f7
Create Date: 2026-06-19 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1e2f3a4d5"
down_revision: str | Sequence[str] | None = "4f92e7ece0f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "estimate_members",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("estimate_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("p_member", sa.Double(), nullable=False),
        sa.Column("weight", sa.Double(), server_default=sa.text("1.0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["estimate_id"], ["estimates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("estimate_members_estimate", "estimate_members", ["estimate_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("estimate_members_estimate", table_name="estimate_members")
    op.drop_table("estimate_members")
