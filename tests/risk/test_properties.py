"""Hypothesis property tests for the risk gate (§4.6 invariant 5: hard caps dominate)."""

from dataclasses import replace

from hypothesis import given
from hypothesis import strategies as st

from pythia.risk import (
    ExposureState,
    RiskLimits,
    RiskReason,
    TradeProposal,
    evaluate,
)

nonneg = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
names = st.sampled_from(["a", "b", "c"])
money_map = st.dictionaries(names, nonneg, max_size=3)

limits_st = st.builds(
    RiskLimits,
    cap_per_trade=nonneg,
    cap_per_market=nonneg,
    cap_per_category=nonneg,
    cap_daily=nonneg,
    cap_weekly=nonneg,
    cap_absolute=nonneg,
    min_source_reliability=unit,
    min_confidence=unit,
    max_p_resol_severity=unit,
)
exposure_st = st.builds(
    ExposureState,
    per_group=money_map,
    per_category=money_map,
    daily_total=nonneg,
    weekly_total=nonneg,
    absolute_total=nonneg,
)
signed_unit = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)
proposal_st = st.builds(
    TradeProposal,
    proposed_size=nonneg,
    category=names,
    correlation_group=names,
    confidence=unit,
    source_reliability=unit,
    p_resol_severity=unit,
    opportunity_score=signed_unit,
    category_calibrated=st.booleans(),
)


@given(proposal_st, limits_st, exposure_st)
def test_approved_size_respects_every_cap(
    proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState
) -> None:
    decision = evaluate(proposal, limits, exposure)
    group = exposure.per_group.get(proposal.correlation_group, 0.0)
    category = exposure.per_category.get(proposal.category, 0.0)
    tol = 1e-6
    assert 0.0 <= decision.approved_size <= proposal.proposed_size + tol
    assert decision.approved_size <= limits.cap_per_trade + tol
    assert decision.approved_size <= max(0.0, limits.cap_per_market - group) + tol
    assert decision.approved_size <= max(0.0, limits.cap_per_category - category) + tol
    assert decision.approved_size <= max(0.0, limits.cap_daily - exposure.daily_total) + tol
    assert decision.approved_size <= max(0.0, limits.cap_weekly - exposure.weekly_total) + tol
    assert decision.approved_size <= max(0.0, limits.cap_absolute - exposure.absolute_total) + tol


@given(proposal_st, limits_st, exposure_st)
def test_gate_failure_forces_zero_size(
    proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState
) -> None:
    gate_failed = (
        proposal.confidence < limits.min_confidence
        or proposal.source_reliability < limits.min_source_reliability
        or proposal.p_resol_severity > limits.max_p_resol_severity
    )
    decision = evaluate(proposal, limits, exposure)
    if gate_failed:
        assert decision.approved_size == 0.0
        assert decision.vetoed


@given(proposal_st, limits_st, exposure_st)
def test_paper_only_mirrors_category_calibration(
    proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState
) -> None:
    decision = evaluate(proposal, limits, exposure)
    assert decision.paper_only == (not proposal.category_calibrated)


# Invariant 6 — no silent bets: any penalty == 0 ⇒ opportunity_score == 0 ⇒ approved_size == 0.
@given(proposal_st, limits_st, exposure_st)
def test_non_positive_opportunity_forces_zero_size(
    proposal: TradeProposal, limits: RiskLimits, exposure: ExposureState
) -> None:
    decision = evaluate(replace(proposal, opportunity_score=0.0), limits, exposure)
    assert decision.approved_size == 0.0
    assert decision.vetoed
    assert RiskReason.ZERO_OPPORTUNITY in decision.reasons
