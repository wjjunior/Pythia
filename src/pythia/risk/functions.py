"""Deterministic risk gate (§4.4): pure caps, gating and correlated-exposure veto.

No LLM, no clock, no I/O. Limits and exposure are injected; gates veto, caps adjust down.
"""

from pythia.risk.models import (
    ExposureState,
    RiskDecision,
    RiskLimits,
    RiskReason,
    TradeProposal,
)


def gate_reasons(proposal: TradeProposal, limits: RiskLimits) -> tuple[RiskReason, ...]:
    reasons: list[RiskReason] = []
    # No silent bets (§4.6): any zeroed penalty collapses opportunity_score, so a non-positive
    # score means a catastrophic quality dimension — never size it.
    if proposal.opportunity_score <= 0.0:
        reasons.append(RiskReason.ZERO_OPPORTUNITY)
    if proposal.confidence < limits.min_confidence:
        reasons.append(RiskReason.MIN_CONFIDENCE)
    if proposal.source_reliability < limits.min_source_reliability:
        reasons.append(RiskReason.MIN_SOURCE_RELIABILITY)
    if proposal.p_resol_severity > limits.max_p_resol_severity:
        reasons.append(RiskReason.MAX_P_RESOL_SEVERITY)
    return tuple(reasons)


def cap_headrooms(
    proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState
) -> dict[RiskReason, float]:
    group = exposure.per_group.get(proposal.correlation_group, 0.0)
    category = exposure.per_category.get(proposal.category, 0.0)
    return {
        RiskReason.CAP_PER_TRADE: limits.cap_per_trade,
        RiskReason.CAP_PER_MARKET: limits.cap_per_market - group,
        RiskReason.CAP_PER_CATEGORY: limits.cap_per_category - category,
        RiskReason.CAP_DAILY: limits.cap_daily - exposure.daily_total,
        RiskReason.CAP_WEEKLY: limits.cap_weekly - exposure.weekly_total,
        RiskReason.CAP_ABSOLUTE: limits.cap_absolute - exposure.absolute_total,
    }


def evaluate(proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState) -> RiskDecision:
    paper_only = not proposal.category_calibrated or proposal.resolution_gated

    gates = gate_reasons(proposal, limits)
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
