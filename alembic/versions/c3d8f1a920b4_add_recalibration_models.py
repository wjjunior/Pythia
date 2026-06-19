"""add recalibration_models (§4.7, PYT-33)

Revision ID: c3d8f1a920b4
Revises: b7c1e2f3a4d5
Create Date: 2026-06-19 13:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d8f1a920b4"
down_revision: str | Sequence[str] | None = "b7c1e2f3a4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "recalibration_models",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("n_fit", sa.Integer(), nullable=False),
        sa.Column(
            "fitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "recalibration_active", "recalibration_models", ["category", "active"], unique=False
    )
    op.create_index(
        "recalibration_one_active",
        "recalibration_models",
        ["category"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("recalibration_one_active", table_name="recalibration_models")
    op.drop_index("recalibration_active", table_name="recalibration_models")
    op.drop_table("recalibration_models")
