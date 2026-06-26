"""append-only immutability: block UPDATE on resolutions (PYT-102, F-07)

resolutions is the asof gate for every scorecard; a post-hoc resolved_at edit would shift
what counts as "before asof". Reuse the existing pythia_reject_update() guard.

Revision ID: e1a7c4d9b2f6
Revises: 7605b039dd76
Create Date: 2026-06-25 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a7c4d9b2f6"
down_revision: str | Sequence[str] | None = "7605b039dd76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "CREATE TRIGGER resolutions_no_update BEFORE UPDATE ON resolutions "
        "FOR EACH ROW EXECUTE FUNCTION pythia_reject_update();"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS resolutions_no_update ON resolutions;")
