"""add markets.event_slug (Polymarket event slug) for the radar deep-link (PYT-83)

Revision ID: c7e9b1d3f5a2
Revises: a1c3e5b7d9f2
Create Date: 2026-06-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e9b1d3f5a2"
down_revision: str | Sequence[str] | None = "a1c3e5b7d9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("markets", sa.Column("event_slug", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("markets", "event_slug")
