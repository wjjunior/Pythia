"""Shared domain contracts (§4.1). Pure Pydantic value objects — zero infrastructure."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Prob = Annotated[float, Field(ge=0.0, le=1.0)]
RecalibrationMethod = Literal["platt", "beta", "isotonic"]


class Side(StrEnum):
    YES = "YES"
    NO = "NO"


class TradeStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    VOID = "void"  # manually removed (PYT-181): capital released, no PnL


class MarketStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class EventType(StrEnum):
    """How an event's option-markets relate (PYT-137)."""

    MUTUALLY_EXCLUSIVE = "mutually_exclusive"  # negRisk: exactly one option resolves YES
    THRESHOLD_LADDER = "threshold_ladder"  # nested "above $X": P non-increasing in the threshold
    UNKNOWN = "unknown"  # not coupled


class ArbKind(StrEnum):
    """A structural arbitrage shape on a multi-option event (PYT-138)."""

    DUTCH_BOOK = "dutch_book"  # mutually-exclusive options whose YES asks sum to < 1
    LADDER_SPREAD = "ladder_spread"  # a threshold ladder priced non-monotonically


class ResolutionSourceType(StrEnum):
    """How a market resolves (§4.4.2) — grades the P_resol penalty and gates promotion."""

    CHAINLINK = "chainlink"
    CLEAN_BINARY = "clean_binary"
    SUBJECTIVE_UMA = "subjective_uma"


class Cohort(StrEnum):
    """Eval cohort labels (§4.11): config-defined market slices for honest calibration."""

    FAST_7D = "fast_7d"
    MIDBAND = "midband"
    OVERALL = "overall"


class OpportunityStatus(StrEnum):
    """The §A.4 opportunity lifecycle vocabulary (discovered → … → resolved)."""

    DISCOVERED = "discovered"
    RESEARCHED = "researched"
    ESTIMATED = "estimated"
    CRITIQUED = "critiqued"
    SCORED = "scored"
    VETOED = "vetoed"
    REVIEW = "review"
    REJECTED = "rejected"
    PAPER_OPEN = "paper_open"
    RESOLVED = "resolved"


class ReviewStatus(StrEnum):
    """Where a market stands in the human-review/paper lifecycle (PYT-153), for the ops views."""

    NONE = "none"  # not yet reviewed
    OPEN = "open"  # has an open paper position
    RESOLVED = "resolved"  # had a paper position that resolved
    REJECTED = "rejected"  # the review gate rejected it


class AuditKind(StrEnum):
    ESTIMATE = "estimate"
    CRITIC_VERDICT = "critic_verdict"
    SCORE = "score"
    RISK_DECISION = "risk_decision"
    APPROVAL = "approval"
    REJECTION = "rejection"
    PAPER_OPEN = "paper_open"
    COHORT_TAGGED = "cohort_tagged"
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
    resolution_source_type: ResolutionSourceType | None = None
    event_slug: str | None = None
    neg_risk: bool = False  # Polymarket negRisk: marks a mutually-exclusive option set
    close_time: datetime | None = None
    resolves_at: datetime | None = None
    resolved: bool = False


class EventOption(_Frozen):
    market_id: str
    question: str
    yes_price: float
    spread: float
    threshold: float | None = None  # parsed for THRESHOLD_LADDER, else None


class EventGroup(_Frozen):
    event_slug: str
    event_type: EventType
    options: list[EventOption]


class ArbLeg(_Frozen):
    market_id: str
    side: Side
    price: float  # the executable price to pay for this leg at detection time
    question: str = ""  # the leg market's question, for a legible panel (PYT-152)


class ArbOpportunity(_Frozen):
    event_slug: str
    kind: ArbKind
    legs: list[ArbLeg]
    ev: float  # locked profit per unit basket (> 0)
    id: str | None = None  # the persisted row id; None for a freshly-detected, unsaved arb
    detected_at: datetime | None = None  # when the event pass detected it; None until persisted


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


class Venue(StrEnum):
    KALSHI = "kalshi"
    MANIFOLD = "manifold"
    METACULUS = "metaculus"


class VenueQuote(_Frozen):
    id: str
    market_id: str
    venue: Venue
    external_ref: str
    ts: datetime
    yes_price: Prob
    match_confidence: Prob


class Evidence(_Frozen):
    id: str
    market_id: str
    source_id: str
    observed_at: datetime
    url: str | None = None
    content: str


class MemberForecast(_Frozen):
    model: str
    p_member: Prob
    weight: float = 1.0


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
    # The recalibrated forecast the deterministic layer sizes on (PYT-100). ``None`` means raw
    # ``p_model`` is the committed value (no active recalibration map, or a pre-PYT-100 row).
    p_model_calibrated: Prob | None = None
    # The fit-asof of the map that produced ``p_model_calibrated`` (PYT-111); ``None`` when raw. The
    # verdict refuses a committed forecast whose map was fit after the snapshot (replay lookahead).
    p_model_calibrated_fit_asof: datetime | None = None
    members: list[MemberForecast] = Field(default_factory=list)


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
    estimate_id: str | None  # None for an arb-basket leg — a price play, not a model forecast
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


class ResolutionProposal(_Frozen):
    market_id: str
    outcome: Literal[0, 1] | None = None
    ambiguous: bool = False
    confidence: Prob = 0.0
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    model_name: str

    @model_validator(mode="after")
    def _outcome_matches_ambiguity(self) -> Self:
        if (self.outcome is None) != self.ambiguous:
            raise ValueError("outcome must be set iff the proposal is not ambiguous")
        return self


class AuditEvent(_Frozen):
    kind: AuditKind
    # heterogeneous JSON ledger payload; shape varies by AuditKind
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


class FailureClass(StrEnum):
    STALE_EVIDENCE = "stale_evidence"
    MISSING_EVIDENCE = "missing_evidence"
    WEAK_SOURCE = "weak_source"
    RESOLUTION_AMBIGUITY = "resolution_ambiguity"
    MODEL_OVERCONFIDENCE = "model_overconfidence"
    WRONG_ROUTING = "wrong_routing"
    PROMPT_GAP = "prompt_gap"
    CALIBRATED_LOSS = "calibrated_loss"


class ImprovementAction(StrEnum):
    REROUTE = "reroute"
    SOURCE = "source"
    PROMPT = "prompt"
    RECALIBRATE = "recalibrate"
    GUARDRAIL = "guardrail"
    GATE_CATEGORY = "gate_category"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATING = "validating"
    ADOPTED = "adopted"
    REJECTED = "rejected"


class FailureCase(_Frozen):
    market_id: str
    estimate_id: str
    category: str
    p_model: Prob
    confidence: Prob
    outcome: int
    surprise: float
    asof: datetime
    resolution_ambiguous: bool
    evidence: list[Evidence] = Field(default_factory=list)


class PostmortemVerdict(_Frozen):
    failure_class: FailureClass
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)


class Postmortem(_Frozen):
    id: str
    market_id: str
    category: str
    failure_class: FailureClass
    brier: float
    rationale: str
    estimate_id: str | None = None
    resolution_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ImprovementCandidate(_Frozen):
    id: str
    postmortem_id: str
    kind: ImprovementAction
    status: CandidateStatus
    rationale: str
    requires_review: bool
    brier_per_dollar: float | None = None
    n: int | None = None
    human_approved: bool | None = None


# ---- v2 smart-money candidate sourcing (Falcon, §4.1) ----


class TraderState(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    BENCHED = "benched"
    RETIRED = "retired"


class CandidateSource(StrEnum):
    NEW_ENTRY = "new_entry"
    OPEN_BACKFILL = "open_backfill"


class CandidateSignalStatus(StrEnum):
    QUEUED = "queued"
    EVALUATED = "evaluated"
    SKIPPED = "skipped"


class TrackedTrader(_Frozen):
    id: str
    wallet: str
    state: TraderState
    added_at: datetime
    state_since: datetime
    note: str | None = None


class TraderScorecard(_Frozen):
    id: str
    trader_id: str
    asof: datetime
    n_resolved: int
    market_class_ok: bool
    trader_score: float
    roi: float | None = None
    risk_adj_edge: float | None = None
    sortino: float | None = None
    skew: float | None = None
    max_drawdown: float | None = None
    extreme_price_share: float | None = None
    agree_rate: float | None = None
    agreed_bpd: float | None = None
    raw: dict[str, Any] | None = None


class TraderEntry(_Frozen):
    id: str
    trader_id: str
    external_market: str
    side: str
    ts: datetime
    is_increase: bool
    market_id: str | None = None
    size_usd: float | None = None
    entry_price: float | None = None


class CandidateSignal(_Frozen):
    id: str
    asof: datetime
    trader_ids: list[str]
    source: CandidateSource
    status: CandidateSignalStatus
    created_at: datetime
    market_id: str | None = None


class RosterEvent(_Frozen):
    id: str
    trader_id: str
    to_state: TraderState
    reason: str
    created_at: datetime
    from_state: TraderState | None = None
    metrics: dict[str, Any] | None = None


class HeldPosition(_Frozen):
    """A position a wallet currently holds at a venue — the PositionsPort boundary shape (§4.5)."""

    condition_id: str
    outcome: str
    size: float
    avg_price: float
    cur_price: float
    current_value: float
    cash_pnl: float
    title: str
    end_date: datetime | None = None


class TraderPosition(_Frozen):
    """A tracked wallet's held position, stamped with the trader and the poll time, as stored."""

    id: str
    trader_id: str
    condition_id: str
    outcome: str
    size: float
    avg_price: float
    cur_price: float
    current_value: float
    cash_pnl: float
    title: str
    asof: datetime
    end_date: datetime | None = None


class PipelineFunnel(_Frozen):
    """Per-tick pipeline stage funnel (PYT-164) — diagnostic counts, never a gate.

    Crosses orchestration → db (a ``pipeline_run_funnel`` row) → log line, so it lives with the
    shared contracts. ``categories`` is the per-category crawl representation.
    """

    crawled: int
    tracked_open: int
    shortlisted: int
    estimated: int
    scored: int
    radar_surfaced: int
    dropped_window: int
    dropped_win_multiple: int
    dropped_win_probability: int
    dropped_extreme_disagreement: int
    categories: dict[str, int]
    truncated: bool = False  # the daily cost cap halted this pass mid-rotation (PYT-168)

    @property
    def reconciles(self) -> bool:
        ordered = (
            self.crawled
            >= self.tracked_open
            >= self.shortlisted
            >= self.estimated
            >= self.scored
            >= self.radar_surfaced
        )
        drops = (
            self.dropped_window
            + self.dropped_win_multiple
            + self.dropped_win_probability
            + self.dropped_extreme_disagreement
        )
        return ordered and drops == self.scored - self.radar_surfaced
