"""Example tests for the risk gate: gates veto, caps adjust, correlated group, paper_only."""

from dataclasses import replace

from pythia.risk import (
    ExposureState,
    RiskLimits,
    RiskReason,
    TradeProposal,
    evaluate,
    gate_reasons,
)

LIMITS = RiskLimits(
    cap_per_trade=10.0,
    cap_per_market=20.0,
    cap_per_category=50.0,
    cap_daily=30.0,
    cap_weekly=100.0,
    cap_absolute=200.0,
    min_source_reliability=0.5,
    min_confidence=0.5,
    max_p_resol_severity=0.5,
)
EMPTY = ExposureState(
    per_group={}, per_category={}, daily_total=0.0, weekly_total=0.0, absolute_total=0.0
)
PROPOSAL = TradeProposal(
    proposed_size=5.0,
    category="politics",
    correlation_group="mkt-1",
    confidence=0.8,
    source_reliability=0.7,
    p_resol_severity=0.2,
    opportunity_score=0.5,
    category_calibrated=True,
)


def test_clean_proposal_is_approved_at_proposed_size() -> None:
    decision = evaluate(PROPOSAL, LIMITS, EMPTY)
    assert decision.approved_size == 5.0
    assert not decision.vetoed
    assert decision.reasons == ()
    assert decision.paper_only is False


def test_poor_resolution_category_is_paper_gated_even_when_calibrated() -> None:
    decision = evaluate(replace(PROPOSAL, resolution_gated=True), LIMITS, EMPTY)
    assert decision.paper_only is True
    assert not decision.vetoed  # gated to paper, still sized normally


def test_low_confidence_vetoes() -> None:
    decision = evaluate(replace(PROPOSAL, confidence=0.4), LIMITS, EMPTY)
    assert decision.vetoed
    assert decision.approved_size == 0.0
    assert RiskReason.MIN_CONFIDENCE in decision.reasons


def test_low_source_reliability_vetoes() -> None:
    decision = evaluate(replace(PROPOSAL, source_reliability=0.3), LIMITS, EMPTY)
    assert decision.vetoed
    assert RiskReason.MIN_SOURCE_RELIABILITY in decision.reasons


def test_high_p_resol_severity_vetoes() -> None:
    decision = evaluate(replace(PROPOSAL, p_resol_severity=0.9), LIMITS, EMPTY)
    assert decision.vetoed
    assert RiskReason.MAX_P_RESOL_SEVERITY in decision.reasons


def test_per_trade_cap_binds() -> None:
    decision = evaluate(replace(PROPOSAL, proposed_size=15.0), LIMITS, EMPTY)
    assert decision.approved_size == 10.0
    assert not decision.vetoed
    assert RiskReason.CAP_PER_TRADE in decision.reasons


def test_per_market_cap_binds_via_correlation_group() -> None:
    exposure = replace(EMPTY, per_group={"mkt-1": 18.0})
    decision = evaluate(PROPOSAL, LIMITS, exposure)
    assert decision.approved_size == 2.0
    assert RiskReason.CAP_PER_MARKET in decision.reasons


def test_correlated_markets_share_one_group_exposure() -> None:
    exposure = replace(EMPTY, per_group={"grp-A": 19.0})
    decision = evaluate(replace(PROPOSAL, correlation_group="grp-A"), LIMITS, exposure)
    assert decision.approved_size == 1.0


def test_exhausted_market_cap_vetoes() -> None:
    exposure = replace(EMPTY, per_group={"mkt-1": 20.0})
    decision = evaluate(PROPOSAL, LIMITS, exposure)
    assert decision.vetoed
    assert decision.approved_size == 0.0
    assert RiskReason.CAP_PER_MARKET in decision.reasons


def test_daily_cap_binds() -> None:
    exposure = replace(EMPTY, daily_total=28.0)
    decision = evaluate(PROPOSAL, LIMITS, exposure)
    assert decision.approved_size == 2.0
    assert RiskReason.CAP_DAILY in decision.reasons


def test_uncalibrated_category_is_paper_only_but_not_vetoed() -> None:
    decision = evaluate(replace(PROPOSAL, category_calibrated=False), LIMITS, EMPTY)
    assert decision.paper_only is True
    assert not decision.vetoed
    assert decision.approved_size == 5.0


def test_gate_reasons_lists_every_violation() -> None:
    proposal = replace(PROPOSAL, confidence=0.1, source_reliability=0.1, p_resol_severity=0.9)
    assert set(gate_reasons(proposal, LIMITS)) == {
        RiskReason.MIN_CONFIDENCE,
        RiskReason.MIN_SOURCE_RELIABILITY,
        RiskReason.MAX_P_RESOL_SEVERITY,
    }


def test_zero_size_proposal_is_not_vetoed() -> None:
    decision = evaluate(replace(PROPOSAL, proposed_size=0.0), LIMITS, EMPTY)
    assert decision.approved_size == 0.0
    assert not decision.vetoed
    assert decision.reasons == ()
