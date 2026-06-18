"""initial schema 4.7

Revision ID: 4f92e7ece0f7
Revises:
Create Date: 2026-06-18 14:54:00.081004

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4f92e7ece0f7"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "audit_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("audit_market_ts", "audit_events", ["market_id", "ts"], unique=False)
    op.create_table(
        "markets",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("resolution_rule", sa.Text(), nullable=False),
        sa.Column("resolution_source", sa.Text(), nullable=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reliability", sa.Double(), server_default=sa.text("0.5"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(dim=1536), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "evidence_embedding",
        "evidence",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("evidence_market_time", "evidence", ["market_id", "observed_at"], unique=False)
    op.create_table(
        "market_snapshots",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yes_price", sa.Double(), nullable=False),
        sa.Column("spread", sa.Double(), nullable=False),
        sa.Column("volume", sa.Double(), nullable=False),
        sa.Column("liquidity", sa.Double(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("snapshots_market_ts", "market_snapshots", ["market_id", "ts"], unique=False)
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_id", sa.UUID(), nullable=True),
        sa.Column("routing", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "resolutions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("outcome", sa.Integer(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("ambiguous", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("outcome IN (0,1)", name="resolutions_outcome_check"),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("market_id"),
    )
    op.create_table(
        "estimates",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("p_model", sa.Double(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("lower", sa.Double(), nullable=False),
        sa.Column("upper", sa.Double(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("critic_notes", sa.Text(), nullable=True),
        sa.Column("evidence_ids", sa.ARRAY(sa.UUID()), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["market_snapshots.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cost_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("pipeline_run_id", sa.UUID(), nullable=True),
        sa.Column("market_id", sa.UUID(), nullable=True),
        sa.Column("estimate_id", sa.UUID(), nullable=True),
        sa.Column("agent", sa.Text(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_usd", sa.Double(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["estimate_id"],
            ["estimates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("cost_agent_ts", "cost_events", ["agent", "ts"], unique=False)
    op.create_index("cost_model_ts", "cost_events", ["model", "ts"], unique=False)
    op.create_index("cost_run", "cost_events", ["pipeline_run_id"], unique=False)
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("market_id", sa.UUID(), nullable=False),
        sa.Column("estimate_id", sa.UUID(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("entry_price", sa.Double(), nullable=False),
        sa.Column("size", sa.Double(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pnl", sa.Double(), nullable=True),
        sa.CheckConstraint("side IN ('YES','NO')", name="paper_trades_side_check"),
        sa.CheckConstraint("status IN ('open','resolved')", name="paper_trades_status_check"),
        sa.ForeignKeyConstraint(
            ["estimate_id"],
            ["estimates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["market_id"],
            ["markets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scores",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("estimate_id", sa.UUID(), nullable=False),
        sa.Column("edge", sa.Double(), nullable=False),
        sa.Column("edge_net", sa.Double(), nullable=False),
        sa.Column("ev_unit", sa.Double(), nullable=False),
        sa.Column("p_resol", sa.Double(), nullable=False),
        sa.Column("p_liq", sa.Double(), nullable=False),
        sa.Column("p_time", sa.Double(), nullable=False),
        sa.Column("p_corr", sa.Double(), nullable=False),
        sa.Column("opportunity_score", sa.Double(), nullable=False),
        sa.Column("kelly_fraction", sa.Double(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["estimate_id"],
            ["estimates.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("estimate_id"),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("scores")
    op.drop_table("paper_trades")
    op.drop_index("cost_run", table_name="cost_events")
    op.drop_index("cost_model_ts", table_name="cost_events")
    op.drop_index("cost_agent_ts", table_name="cost_events")
    op.drop_table("cost_events")
    op.drop_table("estimates")
    op.drop_table("resolutions")
    op.drop_table("pipeline_runs")
    op.drop_index("snapshots_market_ts", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("evidence_market_time", table_name="evidence")
    op.drop_index(
        "evidence_embedding",
        table_name="evidence",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("evidence")
    op.drop_table("sources")
    op.drop_table("markets")
    op.drop_index("audit_market_ts", table_name="audit_events")
    op.drop_table("audit_events")
    # ### end Alembic commands ###
