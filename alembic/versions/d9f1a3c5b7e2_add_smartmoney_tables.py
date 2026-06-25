"""v2 smart-money tables: traders, scorecards, entries, candidates, roster events (PYT-89)

Revision ID: d9f1a3c5b7e2
Revises: c7e9b1d3f5a2
Create Date: 2026-06-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d9f1a3c5b7e2"
down_revision: str | Sequence[str] | None = "c7e9b1d3f5a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_PK_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tracked_traders",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column("wallet", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'trial'"), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "state_since",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("note", sa.Text()),
        sa.UniqueConstraint("wallet", name="tracked_traders_wallet_key"),
        sa.CheckConstraint(
            "state IN ('trial','active','benched','retired')", name="tracked_traders_state_check"
        ),
    )
    op.create_table(
        "trader_scorecards",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column("trader_id", _UUID, sa.ForeignKey("tracked_traders.id"), nullable=False),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n_resolved", sa.Integer(), nullable=False),
        sa.Column("roi", sa.Double()),
        sa.Column("risk_adj_edge", sa.Double()),
        sa.Column("sortino", sa.Double()),
        sa.Column("skew", sa.Double()),
        sa.Column("max_drawdown", sa.Double()),
        sa.Column("agree_rate", sa.Double()),
        sa.Column("agreed_bpd", sa.Double()),
        sa.Column("market_class_ok", sa.Boolean(), nullable=False),
        sa.Column("trader_score", sa.Double(), nullable=False),
        sa.Column("raw", postgresql.JSONB()),
    )
    op.create_index("scorecard_trader_asof", "trader_scorecards", ["trader_id", "asof"])
    op.create_table(
        "trader_entries",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column("trader_id", _UUID, sa.ForeignKey("tracked_traders.id"), nullable=False),
        sa.Column("market_id", _UUID, sa.ForeignKey("markets.id")),
        sa.Column("external_market", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("size_usd", sa.Double()),
        sa.Column("entry_price", sa.Double()),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_increase", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "trader_id", "external_market", "ts", name="trader_entries_trader_market_ts"
        ),
    )
    op.create_index("trader_entries_ts", "trader_entries", ["ts"])
    op.create_table(
        "candidate_signals",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column("market_id", _UUID, sa.ForeignKey("markets.id")),
        sa.Column("asof", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trader_ids", postgresql.ARRAY(_UUID), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'new_entry'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('new_entry','open_backfill')", name="candidate_signals_source_check"
        ),
        sa.CheckConstraint(
            "status IN ('queued','evaluated','skipped')", name="candidate_signals_status_check"
        ),
    )
    op.create_index("candidate_status", "candidate_signals", ["status", "asof"])
    op.create_table(
        "roster_events",
        sa.Column("id", _UUID, server_default=_PK_DEFAULT, primary_key=True),
        sa.Column("trader_id", _UUID, sa.ForeignKey("tracked_traders.id"), nullable=False),
        sa.Column("from_state", sa.Text()),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("roster_events_trader", "roster_events", ["trader_id", "created_at"])
    # Append-only (§4.6): scorecards and roster events are immutable once written. Reuse the v1
    # pythia_reject_update() guard (defined in the append-only migration).
    op.execute(
        "CREATE TRIGGER trader_scorecards_no_update BEFORE UPDATE ON trader_scorecards "
        "FOR EACH ROW EXECUTE FUNCTION pythia_reject_update();"
    )
    op.execute(
        "CREATE TRIGGER roster_events_no_update BEFORE UPDATE ON roster_events "
        "FOR EACH ROW EXECUTE FUNCTION pythia_reject_update();"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS roster_events_no_update ON roster_events;")
    op.execute("DROP TRIGGER IF EXISTS trader_scorecards_no_update ON trader_scorecards;")
    op.drop_table("roster_events")
    op.drop_table("candidate_signals")
    op.drop_table("trader_entries")
    op.drop_table("trader_scorecards")
    op.drop_table("tracked_traders")
