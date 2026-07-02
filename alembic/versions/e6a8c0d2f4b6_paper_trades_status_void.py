"""paper_trades.status allows 'void': manually removed positions (PYT-181)

Revision ID: e6a8c0d2f4b6
Revises: d5f7b9c1e3a4
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6a8c0d2f4b6"
down_revision: str | Sequence[str] | None = "d5f7b9c1e3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "paper_trades_status_check"
_TABLE = "paper_trades"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, "status IN ('open','resolved','void')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, "status IN ('open','resolved')")
