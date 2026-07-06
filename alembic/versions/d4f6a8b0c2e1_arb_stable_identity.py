"""arb_opportunities stable identity: identity_key + first_detected_at/last_seen_at

Revision ID: d4f6a8b0c2e1
Revises: f2b4c8e1a3d7
Create Date: 2026-07-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a8b0c2e1"
down_revision: str | Sequence[str] | None = "f2b4c8e1a3d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# identity_key = event_slug|kind|<sorted leg market_ids joined by '|'> — matches arb_identity_key.
_BACKFILL_IDENTITY = """
UPDATE arb_opportunities a
SET identity_key = a.event_slug || '|' || a.kind || '|' || sub.mids
FROM (
    SELECT a2.id,
           string_agg(leg->>'market_id', '|' ORDER BY leg->>'market_id') AS mids
    FROM arb_opportunities a2,
         LATERAL jsonb_array_elements(a2.legs) AS leg
    GROUP BY a2.id
) sub
WHERE a.id = sub.id
"""

# Before the UNIQUE constraint, drop any accidental duplicate identity (keep earliest).
# None expected: replace_for_event never left cross-pass duplicates, but the add must not fail.
_DEDUP = """
DELETE FROM arb_opportunities a
USING (
    SELECT id,
           row_number() OVER (PARTITION BY identity_key
                              ORDER BY first_detected_at ASC, id ASC) AS rn
    FROM arb_opportunities
) d
WHERE a.id = d.id AND d.rn > 1
"""


def upgrade() -> None:
    op.alter_column("arb_opportunities", "detected_at", new_column_name="first_detected_at")

    op.add_column(
        "arb_opportunities",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE arb_opportunities SET last_seen_at = first_detected_at")
    op.alter_column("arb_opportunities", "last_seen_at", nullable=False)

    op.add_column("arb_opportunities", sa.Column("identity_key", sa.Text(), nullable=True))
    op.execute(_BACKFILL_IDENTITY)
    op.execute(_DEDUP)
    op.alter_column("arb_opportunities", "identity_key", nullable=False)
    op.create_unique_constraint(
        "arb_opportunities_identity_key_key", "arb_opportunities", ["identity_key"]
    )


def downgrade() -> None:
    op.drop_constraint("arb_opportunities_identity_key_key", "arb_opportunities", type_="unique")
    op.drop_column("arb_opportunities", "identity_key")
    op.drop_column("arb_opportunities", "last_seen_at")
    op.alter_column("arb_opportunities", "first_detected_at", new_column_name="detected_at")
