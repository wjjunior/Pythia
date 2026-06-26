"""markets.resolution_source_type CHECK constraint (PYT-130, F-14)

Revision ID: b2d4f6a8c1e3
Revises: a1c3e5f7b9d2
Create Date: 2026-06-26 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d4f6a8c1e3"
down_revision: str | Sequence[str] | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "markets_resolution_source_type_check"
_VALUES = (
    "resolution_source_type IS NULL OR "
    "resolution_source_type IN ('chainlink', 'clean_binary', 'subjective_uma')"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(_CHECK, "markets", _VALUES)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_CHECK, "markets", type_="check")
