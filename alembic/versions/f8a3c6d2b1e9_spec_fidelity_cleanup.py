"""spec-fidelity cleanup: improvement_candidates status CHECK + venue_quotes index (PYT-44)

Revision ID: f8a3c6d2b1e9
Revises: e7f2b9c4d1a8
Create Date: 2026-06-22 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a3c6d2b1e9"
down_revision: str | Sequence[str] | None = "e7f2b9c4d1a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CHECK = "improvement_candidates_status_check"
_STATUS_VALUES = "status IN ('proposed','validating','adopted','rejected')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(_STATUS_CHECK, "improvement_candidates", _STATUS_VALUES)
    op.execute("ALTER INDEX venue_quotes_market_time RENAME TO venue_quotes_market_ts")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER INDEX venue_quotes_market_ts RENAME TO venue_quotes_market_time")
    op.drop_constraint(_STATUS_CHECK, "improvement_candidates", type_="check")
