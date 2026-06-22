"""enforce append-only immutability on market_snapshots & evidence (PYT-46)

AC-2 requires market_snapshots and evidence to have no update path. A BEFORE UPDATE trigger raises
on any in-place mutation — role-independent, unlike REVOKE UPDATE which the table owner bypasses.
DELETE is intentionally left allowed: only in-place mutation breaks the point-in-time guarantee.

Revision ID: f1a2b3c4d5e6
Revises: e7f2b9c4d1a8
Create Date: 2026-06-22 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e7f2b9c4d1a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = ("market_snapshots", "evidence")


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pythia_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING
                MESSAGE = 'append-only table ' || TG_TABLE_NAME || ' is immutable: UPDATE rejected',
                ERRCODE = 'P0001';
        END;
        $$;
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_reject_update "
            f"BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION pythia_reject_mutation();"
        )


def downgrade() -> None:
    for table in _IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_update ON {table};")
    op.execute("DROP FUNCTION IF EXISTS pythia_reject_mutation();")
