"""SQLModel mapping of the §4.7 schema. Table and column names match the design exactly."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel

EMBEDDING_DIM = 1536


def _id() -> Any:
    return Field(
        default=None,
        sa_column=Column(
            UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
        ),
    )


class Market(SQLModel, table=True):
    __tablename__ = "markets"

    id: uuid.UUID | None = _id()
    external_id: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    question: str = Field(sa_column=Column(Text, nullable=False))
    category: str = Field(sa_column=Column(Text, nullable=False))
    resolution_rule: str = Field(sa_column=Column(Text, nullable=False))
    resolution_source: str | None = Field(default=None, sa_column=Column(Text))
    close_time: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    resolves_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    resolved: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class MarketSnapshot(SQLModel, table=True):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("snapshots_market_ts", "market_id", "ts"),)

    id: uuid.UUID | None = _id()
    market_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    )
    ts: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    yes_price: float = Field(sa_column=Column(Double, nullable=False))
    spread: float = Field(sa_column=Column(Double, nullable=False))
    volume: float = Field(sa_column=Column(Double, nullable=False))
    liquidity: float = Field(sa_column=Column(Double, nullable=False))
    raw: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: uuid.UUID | None = _id()
    name: str = Field(sa_column=Column(Text, nullable=False, unique=True))
    kind: str = Field(sa_column=Column(Text, nullable=False))
    reliability: float = Field(
        default=0.5,
        sa_column=Column(Double, nullable=False, server_default=text("0.5")),
    )


class Evidence(SQLModel, table=True):
    __tablename__ = "evidence"
    __table_args__ = (
        Index("evidence_market_time", "market_id", "observed_at"),
        Index(
            "evidence_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: uuid.UUID | None = _id()
    market_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    )
    source_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False)
    )
    observed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    url: str | None = Field(default=None, sa_column=Column(Text))
    content: str = Field(sa_column=Column(Text, nullable=False))
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector(EMBEDDING_DIM)))
    meta: dict[str, Any] | None = Field(default=None, sa_column=Column("metadata", JSONB))


class Estimate(SQLModel, table=True):
    __tablename__ = "estimates"

    id: uuid.UUID | None = _id()
    market_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    )
    snapshot_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("market_snapshots.id"), nullable=False)
    )
    p_model: float = Field(sa_column=Column(Double, nullable=False))
    confidence: float = Field(sa_column=Column(Double, nullable=False))
    lower: float = Field(sa_column=Column(Double, nullable=False))
    upper: float = Field(sa_column=Column(Double, nullable=False))
    rationale: str = Field(sa_column=Column(Text, nullable=False))
    critic_notes: str | None = Field(default=None, sa_column=Column(Text))
    evidence_ids: list[uuid.UUID] = Field(
        sa_column=Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    )
    model_name: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class EstimateMember(SQLModel, table=True):
    __tablename__ = "estimate_members"
    __table_args__ = (Index("estimate_members_estimate", "estimate_id"),)

    id: uuid.UUID | None = _id()
    estimate_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("estimates.id"), nullable=False)
    )
    model: str = Field(sa_column=Column(Text, nullable=False))
    p_member: float = Field(sa_column=Column(Double, nullable=False))
    weight: float = Field(
        default=1.0,
        sa_column=Column(Double, nullable=False, server_default=text("1.0")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class RecalibrationModel(SQLModel, table=True):
    __tablename__ = "recalibration_models"
    __table_args__ = (
        Index("recalibration_active", "category", "active"),
        Index(
            "recalibration_one_active",
            "category",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: uuid.UUID | None = _id()
    category: str = Field(sa_column=Column(Text, nullable=False))
    method: str = Field(sa_column=Column(Text, nullable=False))
    params: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    n_fit: int = Field(sa_column=Column(Integer, nullable=False))
    fitted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )


class Score(SQLModel, table=True):
    __tablename__ = "scores"

    id: uuid.UUID | None = _id()
    estimate_id: uuid.UUID = Field(
        sa_column=Column(
            UUID(as_uuid=True), ForeignKey("estimates.id"), nullable=False, unique=True
        )
    )
    edge: float = Field(sa_column=Column(Double, nullable=False))
    edge_net: float = Field(sa_column=Column(Double, nullable=False))
    ev_unit: float = Field(sa_column=Column(Double, nullable=False))
    p_resol: float = Field(sa_column=Column(Double, nullable=False))
    p_liq: float = Field(sa_column=Column(Double, nullable=False))
    p_time: float = Field(sa_column=Column(Double, nullable=False))
    p_corr: float = Field(sa_column=Column(Double, nullable=False))
    opportunity_score: float = Field(sa_column=Column(Double, nullable=False))
    kelly_fraction: float = Field(sa_column=Column(Double, nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class PaperTrade(SQLModel, table=True):
    __tablename__ = "paper_trades"
    __table_args__ = (
        CheckConstraint("side IN ('YES','NO')", name="paper_trades_side_check"),
        CheckConstraint("status IN ('open','resolved')", name="paper_trades_status_check"),
    )

    id: uuid.UUID | None = _id()
    market_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    )
    estimate_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("estimates.id"), nullable=False)
    )
    side: str = Field(sa_column=Column(Text, nullable=False))
    entry_price: float = Field(sa_column=Column(Double, nullable=False))
    size: float = Field(sa_column=Column(Double, nullable=False))
    status: str = Field(
        default="open",
        sa_column=Column(Text, nullable=False, server_default=text("'open'")),
    )
    opened_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    resolved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    pnl: float | None = Field(default=None, sa_column=Column(Double))


class Resolution(SQLModel, table=True):
    __tablename__ = "resolutions"
    __table_args__ = (CheckConstraint("outcome IN (0,1)", name="resolutions_outcome_check"),)

    id: uuid.UUID | None = _id()
    market_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False, unique=True)
    )
    outcome: int = Field(sa_column=Column(Integer, nullable=False))
    resolved_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    source: str | None = Field(default=None, sa_column=Column(Text))
    ambiguous: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"
    __table_args__ = (Index("audit_market_ts", "market_id", "ts"),)

    id: uuid.UUID | None = _id()
    ts: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    kind: str = Field(sa_column=Column(Text, nullable=False))
    market_id: uuid.UUID | None = Field(default=None, sa_column=Column(UUID(as_uuid=True)))
    payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))


class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_runs"

    id: uuid.UUID | None = _id()
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    market_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"))
    )
    routing: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    status: str = Field(
        default="running",
        sa_column=Column(Text, nullable=False, server_default=text("'running'")),
    )


class CostEvent(SQLModel, table=True):
    __tablename__ = "cost_events"
    __table_args__ = (
        Index("cost_run", "pipeline_run_id"),
        Index("cost_agent_ts", "agent", "ts"),
        Index("cost_model_ts", "model", "ts"),
    )

    id: uuid.UUID | None = _id()
    ts: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    pipeline_run_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(UUID(as_uuid=True), ForeignKey("pipeline_runs.id"))
    )
    market_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"))
    )
    estimate_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(UUID(as_uuid=True), ForeignKey("estimates.id"))
    )
    agent: str = Field(sa_column=Column(Text, nullable=False))
    task: str = Field(sa_column=Column(Text, nullable=False))
    provider: str = Field(sa_column=Column(Text, nullable=False))
    model: str = Field(sa_column=Column(Text, nullable=False))
    input_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    output_tokens: int = Field(sa_column=Column(Integer, nullable=False))
    cached_tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    cost_usd: float = Field(sa_column=Column(Double, nullable=False))
    latency_ms: int | None = Field(default=None, sa_column=Column(Integer))
    correlation_id: str | None = Field(default=None, sa_column=Column(Text))


class VenueQuote(SQLModel, table=True):
    __tablename__ = "venue_quotes"
    __table_args__ = (Index("venue_quotes_market_ts", "market_id", "ts"),)

    id: uuid.UUID | None = _id()
    market_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    )
    venue: str = Field(sa_column=Column(Text, nullable=False))
    external_ref: str = Field(sa_column=Column(Text, nullable=False))
    ts: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    yes_price: float = Field(sa_column=Column(Double, nullable=False))
    match_confidence: float = Field(sa_column=Column(Double, nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class Postmortem(SQLModel, table=True):
    __tablename__ = "postmortems"
    __table_args__ = (Index("postmortems_category", "category", "created_at"),)

    id: uuid.UUID | None = _id()
    market_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False)
    )
    estimate_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(UUID(as_uuid=True), ForeignKey("estimates.id"))
    )
    resolution_id: uuid.UUID | None = Field(
        default=None, sa_column=Column(UUID(as_uuid=True), ForeignKey("resolutions.id"))
    )
    category: str = Field(sa_column=Column(Text, nullable=False))
    failure_class: str = Field(sa_column=Column(Text, nullable=False))
    brier: float = Field(sa_column=Column(Double, nullable=False))
    rationale: str = Field(sa_column=Column(Text, nullable=False))
    evidence_ids: list[uuid.UUID] = Field(
        sa_column=Column(ARRAY(UUID(as_uuid=True)), nullable=False)
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )


class ImprovementCandidate(SQLModel, table=True):
    __tablename__ = "improvement_candidates"
    __table_args__ = (
        Index("improvement_status", "status", "created_at"),
        CheckConstraint(
            "status IN ('proposed','validating','adopted','rejected')",
            name="improvement_candidates_status_check",
        ),
    )

    id: uuid.UUID | None = _id()
    postmortem_id: uuid.UUID = Field(
        sa_column=Column(UUID(as_uuid=True), ForeignKey("postmortems.id"), nullable=False)
    )
    kind: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(sa_column=Column(Text, nullable=False))
    rationale: str = Field(sa_column=Column(Text, nullable=False))
    requires_review: bool = Field(sa_column=Column(Boolean, nullable=False))
    brier_per_dollar: float | None = Field(default=None, sa_column=Column(Double))
    n: int | None = Field(default=None, sa_column=Column(Integer))
    human_approved: bool | None = Field(default=None, sa_column=Column(Boolean))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
