"""add estimates.p_model_calibrated (committed forecast for the verdict) (PYT-100)

Revision ID: df6df0b5a0a2
Revises: 7605b039dd76
Create Date: 2026-06-25 23:33:48.257383

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "df6df0b5a0a2"
down_revision: str | Sequence[str] | None = "7605b039dd76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("estimates", sa.Column("p_model_calibrated", sa.Double(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("estimates", "p_model_calibrated")
