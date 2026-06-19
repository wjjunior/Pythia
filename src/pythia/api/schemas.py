"""API request/response models (§A.1). The §4.1 contracts cross the boundary directly; the composite
shapes are defined here. These are the OpenAPI source of truth for the generated TS types."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from pythia.common import Estimate, Evidence, Market, MarketSnapshot, PaperTrade, Score, Side
from pythia.eval.models import CategoryCalibration, EvCalibration, ReliabilityBin


class MarketDetail(BaseModel):
    market: Market
    snapshots: list[MarketSnapshot]
    latest_estimate: Estimate | None
    evidence: list[Evidence]


class RadarItem(BaseModel):
    market: Market
    estimate: Estimate
    score: Score


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


class HealthResponse(BaseModel):
    status: str
    database: bool
    last_snapshot_at: datetime | None
    last_run_at: datetime | None
    queue_age_seconds: float | None
