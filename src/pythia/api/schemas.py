"""API request/response models (§A.1). The §4.1 contracts cross the boundary directly; the composite
shapes are defined here. These are the OpenAPI source of truth for the generated TS types."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from pythia.common import (
    CandidateSignal,
    Estimate,
    Evidence,
    Market,
    MarketSnapshot,
    PaperTrade,
    ReviewStatus,
    RosterEvent,
    Score,
    Side,
    TrackedTrader,
    TraderEntry,
    TraderPosition,
)
from pythia.eval.gate import GateDecision
from pythia.eval.models import CategoryCalibration, CohortCalibration, EvCalibration, ReliabilityBin


class MarketDetail(BaseModel):
    market: Market
    snapshots: list[MarketSnapshot]
    latest_estimate: Estimate | None
    evidence: list[Evidence]


class RadarItem(BaseModel):
    market: Market
    estimate: Estimate
    score: Score
    recommended_side: Side
    entry_price: float
    win_multiple: float
    win_probability: float
    closes_in_days: float | None
    resolves_in_days: float | None
    review_status: ReviewStatus


class EstimateDetail(BaseModel):
    estimate: Estimate
    evidence: list[Evidence]


class CapitalState(BaseModel):
    starting_bankroll: float
    realized_pnl: float
    locked_in_open: float
    available_capital: float
    exposure_fraction: float


class PaperPortfolio(BaseModel):
    positions: list[PaperTrade]
    capital: CapitalState


class ExposureView(BaseModel):
    per_category: dict[str, float]
    per_correlation_group: dict[str, float]
    daily_total: float
    weekly_total: float
    absolute_total: float


class PortfolioResponse(BaseModel):
    capital: CapitalState
    exposure: ExposureView


class CalibrationResponse(BaseModel):
    categories: list[CategoryCalibration]
    reliability: list[ReliabilityBin]
    ev: EvCalibration | None
    cohorts: list[CohortCalibration]
    # The §8.5 gate per category: cleared iff Brier(system) beats the market by margin over min-n,
    # EV honest, resolution ok. The Brier/n/invalid components live in `categories` (join by name).
    gates: list[GateDecision]


class CostResponse(BaseModel):
    total: float
    cost_per_opportunity: float
    by_agent: dict[str, float]
    by_model: dict[str, float]
    by_category: dict[str, float]
    by_day: dict[str, float]


class ArbLegItem(BaseModel):
    market_id: str
    side: str
    price: float
    question: str


class ArbItem(BaseModel):
    id: str
    event_slug: str
    kind: str
    ev: float
    legs: list[ArbLegItem]
    detected_at: datetime | None
    approved: bool


class ArbApproveRequest(BaseModel):
    # unit baskets to open; each leg buys this many shares (size = baskets * price)
    baskets: float = Field(gt=0.0)
    reviewer: str | None = None


class ArbApproveResult(BaseModel):
    arb_id: str
    trades: list[PaperTrade]


class RunResponse(BaseModel):
    markets: int
    scored: int
    halted: bool
    arbs: int = 0


class GrowthCurvePointView(BaseModel):
    kelly_mult: float
    log_growth_rate: float


class SimulationResponse(BaseModel):
    category: str | None
    n: int
    paths: int
    horizon: int
    starting_bankroll: float
    terminal_p5: float
    terminal_p50: float
    terminal_p95: float
    log_growth_rate: float
    mean_max_drawdown: float
    p95_max_drawdown: float
    probability_of_ruin: float
    ruin_floor: float
    growth_curve: list[GrowthCurvePointView]


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ReviewRequest(BaseModel):
    action: ReviewAction
    side: Side | None = None
    size: float | None = None
    reviewer: str | None = None


class ReviewResult(BaseModel):
    estimate_id: str
    action: ReviewAction
    trade: PaperTrade | None = None


class ResolutionRequest(BaseModel):
    outcome: Literal[0, 1]
    ambiguous: bool = False
    source: str | None = None
    at: datetime | None = None


class ResolutionResult(BaseModel):
    market_id: str
    outcome: int
    ambiguous: bool
    postmortem_created: bool


class HealthResponse(BaseModel):
    status: str
    database: bool
    last_snapshot_at: datetime | None
    last_run_at: datetime | None
    queue_age_seconds: float | None


# ---- v2 smart-money dashboard (§6) ----


class ScorecardView(BaseModel):
    """A scorecard for the UI: non-finite floats (the ``-inf`` hard floor) become ``null``."""

    asof: datetime
    n_resolved: int
    market_class_ok: bool
    trader_score: float | None
    roi: float | None
    risk_adj_edge: float | None
    sortino: float | None
    skew: float | None
    max_drawdown: float | None
    agree_rate: float | None
    agreed_bpd: float | None


class RosterEntry(BaseModel):
    trader: TrackedTrader
    scorecard: ScorecardView | None
    last_entry: TraderEntry | None
    reason: str | None  # the latest roster_event reason — why the wallet holds its slot


class CandidateFeedItem(BaseModel):
    candidate: CandidateSignal
    market: Market | None
    viable: bool
    opportunity_score: float | None
    recommended_side: Side | None
    review_status: ReviewStatus


class RosterEventItem(BaseModel):
    event: RosterEvent
    wallet: str


class TraderPositionItem(BaseModel):
    position: TraderPosition
    wallet: str  # the holding wallet, joined from the roster


class OverrideAction(StrEnum):
    PIN = "pin"  # force-keep → active
    MUTE = "mute"  # force-bench → benched


class OverrideRequest(BaseModel):
    action: OverrideAction
