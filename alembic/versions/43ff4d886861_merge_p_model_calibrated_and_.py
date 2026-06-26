"""merge divergent heads: p_model_calibrated (PYT-100) + resolutions immutability (PYT-102)

PR #87 and #88 both branched off 7605b039dd76 and merged in parallel, leaving two Alembic heads.
This no-op merge reunifies them so `upgrade head` is unambiguous again.

Revision ID: 43ff4d886861
Revises: df6df0b5a0a2, e1a7c4d9b2f6
Create Date: 2026-06-26 00:02:11.353170

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "43ff4d886861"
down_revision: str | Sequence[str] | None = ("df6df0b5a0a2", "e1a7c4d9b2f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
