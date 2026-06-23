"""add markets.resolves_at (UMA resolution date) for radar resolution gating (PYT-82)

Revision ID: a1c3e5b7d9f2
Revises: b2f4a6c8d1e3
Create Date: 2026-06-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c3e5b7d9f2"
down_revision: str | Sequence[str] | None = "b2f4a6c8d1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("markets", sa.Column("resolves_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("markets", "resolves_at")
