"""shortlist_cursor: persistent rotating discovery-shortlist cursor (PYT-165)

Revision ID: c4e6a8b0d2f3
Revises: b3d5f7a9c1e4
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4e6a8b0d2f3"
down_revision: str | Sequence[str] | None = "b3d5f7a9c1e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "shortlist_cursor",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("last_market_id", _UUID),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("shortlist_cursor")
