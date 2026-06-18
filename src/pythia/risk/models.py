"""Value objects for the deterministic risk gate (§4.4). Frozen, framework-free."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class RiskReason(Enum):
    MIN_CONFIDENCE = "min_confidence"
    MIN_SOURCE_RELIABILITY = "min_source_reliability"
    MAX_P_RESOL_SEVERITY = "max_p_resol_severity"
    CAP_PER_TRADE = "cap_per_trade"
    CAP_PER_MARKET = "cap_per_market"
    CAP_PER_CATEGORY = "cap_per_category"
    CAP_DAILY = "cap_daily"
    CAP_WEEKLY = "cap_weekly"
    CAP_ABSOLUTE = "cap_absolute"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    cap_per_trade: float
    cap_per_market: float
    cap_per_category: float
    cap_daily: float
    cap_weekly: float
    cap_absolute: float
    min_source_reliability: float
    min_confidence: float
    max_p_resol_severity: float


@dataclass(frozen=True, slots=True)
class ExposureState:
    per_group: Mapping[str, float]
    per_category: Mapping[str, float]
    daily_total: float
    weekly_total: float
    absolute_total: float


@dataclass(frozen=True, slots=True)
class TradeProposal:
    proposed_size: float
    category: str
    correlation_group: str
    confidence: float
    source_reliability: float
    p_resol_severity: float
    category_calibrated: bool


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved_size: float
    vetoed: bool
    reasons: tuple[RiskReason, ...]
    paper_only: bool
