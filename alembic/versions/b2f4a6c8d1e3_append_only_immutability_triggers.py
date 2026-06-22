"""append-only immutability: block UPDATE on market_snapshots & evidence (AC-2, PYT-46)

Revision ID: b2f4a6c8d1e3
Revises: f8a3c6d2b1e9
Create Date: 2026-06-22 14:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2f4a6c8d1e3"
down_revision: str | Sequence[str] | None = "f8a3c6d2b1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pythia_reject_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'append-only table (AC-2): UPDATE is not allowed';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER market_snapshots_no_update BEFORE UPDATE ON market_snapshots "
        "FOR EACH ROW EXECUTE FUNCTION pythia_reject_update();"
    )
    op.execute(
        "CREATE TRIGGER evidence_no_update BEFORE UPDATE ON evidence "
        "FOR EACH ROW EXECUTE FUNCTION pythia_reject_update();"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS market_snapshots_no_update ON market_snapshots;")
    op.execute("DROP TRIGGER IF EXISTS evidence_no_update ON evidence;")
    op.execute("DROP FUNCTION IF EXISTS pythia_reject_update();")
