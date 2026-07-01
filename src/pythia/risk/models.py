"""Value objects for the deterministic risk gate (§4.4). Frozen, framework-free."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from pythia.common import Side


class RiskReason(Enum):
    ZERO_OPPORTUNITY = "zero_opportunity"
    MIN_CONFIDENCE = "min_confidence"
    MIN_SOURCE_RELIABILITY = "min_source_reliability"
    MAX_P_RESOL_SEVERITY = "max_p_resol_severity"
    EVENT_DOUBLE_YES = "event_double_yes"  # 2nd directional YES on a mutually-exclusive event
    CAP_PER_TRADE = "cap_per_trade"
    CAP_PER_MARKET = "cap_per_market"
    CAP_PER_CATEGORY = "cap_per_category"
    CAP_PER_EVENT = "cap_per_event"
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
    cap_per_event: float = 1.0  # total exposure across one event's options; 1.0 ⇒ effectively off
    # Categories (lowercased) where news evidence is meaningful and thus hard-required (PYT-177).
    # Empty ⇒ evidence is never a hard-veto; it still raises confidence via the critic when present.
    evidence_required_categories: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ExposureState:
    per_group: Mapping[str, float]
    per_category: Mapping[str, float]
    daily_total: float
    weekly_total: float
    absolute_total: float
    per_event: Mapping[str, float] = field(default_factory=dict)
    event_open_yes: Mapping[str, int] = field(default_factory=dict)  # open YES legs per event


@dataclass(frozen=True, slots=True)
class TradeProposal:
    proposed_size: float
    category: str
    correlation_group: str
    confidence: float
    source_reliability: float
    p_resol_severity: float
    opportunity_score: float
    category_calibrated: bool
    resolution_gated: bool = False
    side: Side = Side.YES
    event_slug: str | None = None
    event_exclusive: bool = False  # the event is a mutually-exclusive (negRisk) set
    arb_basket: bool = False  # this leg is part of a detected, locked arb basket


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved_size: float
    vetoed: bool
    reasons: tuple[RiskReason, ...]
    paper_only: bool
