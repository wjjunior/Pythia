"""add markets.resolution_source_type (persist the graded resolution-source type) (PYT-122)

Revision ID: a1c3e5f7b9d2
Revises: c5e9a3b7f1d4
Create Date: 2026-06-26 15:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c3e5f7b9d2"
down_revision: str | Sequence[str] | None = "c5e9a3b7f1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("markets", sa.Column("resolution_source_type", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("markets", "resolution_source_type")
