"""Shared domain contracts (§4.1). Pure Pydantic value objects — zero infrastructure."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

Prob = Annotated[float, Field(ge=0.0, le=1.0)]


class Side(StrEnum):
    YES = "YES"
    NO = "NO"


class TradeStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class AuditKind(StrEnum):
    ESTIMATE = "estimate"
    CRITIC_VERDICT = "critic_verdict"
    SCORE = "score"
    RISK_DECISION = "risk_decision"
    APPROVAL = "approval"
    PAPER_OPEN = "paper_open"
    RESOLUTION = "resolution"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Market(_Frozen):
    id: str
    external_id: str
    question: str
    category: str
    resolution_rule: str
    resolution_source: str | None = None
    close_time: datetime | None = None
    resolved: bool = False


class MarketSnapshot(_Frozen):
    id: str
    market_id: str
    ts: datetime
    yes_price: Prob
    spread: float
    volume: float
    liquidity: float


class Source(_Frozen):
    id: str
    name: str
    kind: str
    reliability: Prob


class Evidence(_Frozen):
    id: str
    market_id: str
    source_id: str
    observed_at: datetime
    url: str | None = None
    content: str


class Estimate(_Frozen):
    id: str
    market_id: str
    snapshot_id: str
    p_model: Prob
    confidence: Prob
    lower: Prob
    upper: Prob
    rationale: str
    evidence_ids: list[str]
    model_name: str
    critic_notes: str | None = None


class Score(_Frozen):
    estimate_id: str
    edge: float
    edge_net: float
    ev_unit: float
    p_resol: Prob
    p_liq: Prob
    p_time: Prob
    p_corr: Prob
    opportunity_score: float
    kelly_fraction: float


class PaperTrade(_Frozen):
    id: str
    market_id: str
    estimate_id: str
    side: Side
    entry_price: Prob
    size: float
    status: TradeStatus
    opened_at: datetime
    resolved_at: datetime | None = None
    pnl: float | None = None


class EstimationContext(_Frozen):
    market: Market
    snapshot: MarketSnapshot
    evidence: list[Evidence] = Field(default_factory=list)


class CriticVerdict(_Frozen):
    adjusted_confidence: Prob
    objections: list[str] = Field(default_factory=list)
    lookahead_leak: bool = False
    notes: str | None = None


class AuditEvent(_Frozen):
    kind: AuditKind
    payload: dict[str, Any]
    market_id: str | None = None
    ts: datetime | None = None


class CostEvent(_Frozen):
    agent: str
    task: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached_tokens: int = 0
    latency_ms: int | None = None
    correlation_id: str | None = None
