"""Deterministic risk gate (§4.4): pure caps, gating and correlated-exposure veto.

No LLM, no clock, no I/O. Limits and exposure are injected; gates veto, caps adjust down.
"""

from pythia.common import Side
from pythia.risk.models import (
    ExposureState,
    RiskDecision,
    RiskLimits,
    RiskReason,
    TradeProposal,
)


def cap_size_by_liquidity(size: float, liquidity: float) -> tuple[float, RiskReason | None]:
    """Cap proposed size by available liquidity.

    Returns the smaller of size and liquidity, plus a reason if size exceeds liquidity.
    """
    if size > liquidity:
        return liquidity, RiskReason.SIZE_EXCEEDS_LIQUIDITY
    return size, None


def gate_reasons(proposal: TradeProposal, limits: RiskLimits) -> tuple[RiskReason, ...]:
    reasons: list[RiskReason] = []
    # No silent bets (§4.6): any zeroed penalty collapses opportunity_score, so a non-positive
    # score means a catastrophic quality dimension — never size it.
    if proposal.opportunity_score <= 0.0:
        reasons.append(RiskReason.ZERO_OPPORTUNITY)
    if proposal.confidence < limits.min_confidence:
        reasons.append(RiskReason.MIN_CONFIDENCE)
    # News evidence is a hard requirement only where it is meaningful (PYT-177). Elsewhere (sports,
    # weather, short-term) no article predicts the outcome, so requiring one would block the
    # calibration data the engine collects; evidence still raises confidence via the critic.
    if (
        proposal.category.lower() in limits.evidence_required_categories
        and proposal.source_reliability < limits.min_source_reliability
    ):
        reasons.append(RiskReason.MIN_SOURCE_RELIABILITY)
    if proposal.p_resol_severity > limits.max_p_resol_severity:
        reasons.append(RiskReason.MAX_P_RESOL_SEVERITY)
    return tuple(reasons)


def event_double_yes(proposal: TradeProposal, exposure: ExposureState) -> bool:
    # A 2nd directional YES on a mutually-exclusive event is a self-inflicted dutch book (the legs
    # can't both win) — vetoed, unless they form a single detected, locked arb basket.
    if proposal.arb_basket or not proposal.event_exclusive or proposal.side is not Side.YES:
        return False
    if proposal.event_slug is None:
        return False
    return exposure.event_open_yes.get(proposal.event_slug, 0) > 0


def cap_headrooms(
    proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState
) -> dict[RiskReason, float]:
    group = exposure.per_group.get(proposal.correlation_group, 0.0)
    category = exposure.per_category.get(proposal.category, 0.0)
    headrooms = {
        RiskReason.CAP_PER_TRADE: limits.cap_per_trade,
        RiskReason.CAP_PER_MARKET: limits.cap_per_market - group,
        RiskReason.CAP_PER_CATEGORY: limits.cap_per_category - category,
        RiskReason.CAP_DAILY: limits.cap_daily - exposure.daily_total,
        RiskReason.CAP_WEEKLY: limits.cap_weekly - exposure.weekly_total,
        RiskReason.CAP_ABSOLUTE: limits.cap_absolute - exposure.absolute_total,
    }
    if proposal.event_slug is not None:  # the per-event cap binds only on coupled-event legs
        event = exposure.per_event.get(proposal.event_slug, 0.0)
        headrooms[RiskReason.CAP_PER_EVENT] = limits.cap_per_event - event
    return headrooms


def evaluate(proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState) -> RiskDecision:
    paper_only = not proposal.category_calibrated or proposal.resolution_gated

    gates = gate_reasons(proposal, limits)
    if event_double_yes(proposal, exposure):
        gates = (*gates, RiskReason.EVENT_DOUBLE_YES)
    if gates:
        return RiskDecision(approved_size=0.0, vetoed=True, reasons=gates, paper_only=paper_only)

    headrooms = cap_headrooms(proposal, limits, exposure)
    raw_min = min(proposal.proposed_size, *headrooms.values())
    approved_size = max(0.0, raw_min)

    reasons: tuple[RiskReason, ...]
    if raw_min < proposal.proposed_size:
        reasons = tuple(reason for reason, headroom in headrooms.items() if headroom == raw_min)
    else:
        reasons = ()

    return RiskDecision(
        approved_size=approved_size,
        vetoed=approved_size == 0.0 and bool(reasons),
        paper_only=paper_only,
        reasons=reasons,
    )
