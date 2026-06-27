"""Event-level risk: double-YES veto + per-event exposure cap (PYT-140)."""

from dataclasses import replace

from hypothesis import given
from hypothesis import strategies as st

from pythia.common import Side
from pythia.risk import ExposureState, RiskLimits, RiskReason, TradeProposal, evaluate

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
    cap_per_event=8.0,
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
    side=Side.YES,
    event_slug="evt",
    event_exclusive=True,
)


def _exposure(
    *, per_event: dict[str, float] | None = None, event_open_yes: dict[str, int] | None = None
) -> ExposureState:
    return ExposureState(
        per_group={},
        per_category={},
        daily_total=0.0,
        weekly_total=0.0,
        absolute_total=0.0,
        per_event=per_event or {},
        event_open_yes=event_open_yes or {},
    )


def test_first_yes_leg_on_an_exclusive_event_is_allowed() -> None:
    decision = evaluate(PROPOSAL, LIMITS, _exposure())
    assert not decision.vetoed and decision.approved_size == 5.0


def test_second_yes_leg_on_an_exclusive_event_is_vetoed() -> None:
    decision = evaluate(PROPOSAL, LIMITS, _exposure(event_open_yes={"evt": 1}))
    assert decision.vetoed and decision.approved_size == 0.0
    assert RiskReason.EVENT_DOUBLE_YES in decision.reasons


def test_a_no_leg_is_allowed_even_with_an_open_yes() -> None:
    decision = evaluate(
        replace(PROPOSAL, side=Side.NO), LIMITS, _exposure(event_open_yes={"evt": 1})
    )
    assert not decision.vetoed


def test_a_yes_leg_on_a_different_event_is_allowed() -> None:
    decision = evaluate(PROPOSAL, LIMITS, _exposure(event_open_yes={"other": 1}))
    assert not decision.vetoed


def test_an_arb_basket_leg_is_exempt_from_the_double_yes_veto() -> None:
    decision = evaluate(
        replace(PROPOSAL, arb_basket=True), LIMITS, _exposure(event_open_yes={"evt": 1})
    )
    assert not decision.vetoed


def test_per_event_cap_adjusts_the_size_down() -> None:
    decision = evaluate(PROPOSAL, LIMITS, _exposure(per_event={"evt": 6.0}))  # cap 8 → headroom 2
    assert decision.approved_size == 2.0
    assert RiskReason.CAP_PER_EVENT in decision.reasons


def test_a_non_event_proposal_is_not_limited_by_the_per_event_cap() -> None:
    proposal = replace(PROPOSAL, event_slug=None, event_exclusive=False)
    decision = evaluate(proposal, replace(LIMITS, cap_per_event=0.5), _exposure())
    assert decision.approved_size == 5.0  # event_slug None → CAP_PER_EVENT not applied


@given(proposed=st.floats(0.1, 100.0), existing=st.floats(0.0, 7.0))
def test_per_event_cap_is_a_hard_ceiling(proposed: float, existing: float) -> None:
    proposal = replace(PROPOSAL, proposed_size=proposed)
    decision = evaluate(proposal, LIMITS, _exposure(per_event={"evt": existing}))
    assert decision.approved_size <= LIMITS.cap_per_event - existing + 1e-9
