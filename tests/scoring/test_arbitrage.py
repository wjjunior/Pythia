"""Unit + property tests for structural arbitrage detection (PYT-138)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.common import ArbKind, EventGroup, EventOption, EventType, Side
from pythia.scoring.events import detect_arbitrage


def _opt(mid: str, yes: float, spread: float = 0.02, threshold: float | None = None) -> EventOption:
    return EventOption(
        market_id=mid, question="q", yes_price=yes, spread=spread, threshold=threshold
    )


def _exclusive(*prices: float, spread: float = 0.02) -> EventGroup:
    return EventGroup(
        event_slug="e",
        event_type=EventType.MUTUALLY_EXCLUSIVE,
        options=[_opt(str(i), p, spread) for i, p in enumerate(prices)],
    )


def _ladder(*price_threshold: tuple[float, float]) -> EventGroup:
    options = [_opt(str(i), p, threshold=t) for i, (p, t) in enumerate(price_threshold)]
    options.sort(key=lambda opt: opt.threshold or 0.0)
    return EventGroup(
        event_slug="e",
        event_type=EventType.THRESHOLD_LADDER,
        options=options,
    )


def test_dutch_book_when_yes_asks_sum_below_one() -> None:
    group = _exclusive(0.30, 0.30, 0.30)  # ask 0.31 each, sum 0.93 < 1
    [arb] = detect_arbitrage(group)
    assert arb.kind is ArbKind.DUTCH_BOOK
    assert arb.ev == pytest.approx(1.0 - 3 * 0.31)
    assert {leg.side for leg in arb.legs} == {Side.YES}
    assert len(arb.legs) == 3


def test_no_dutch_book_when_asks_sum_to_at_least_one() -> None:
    assert detect_arbitrage(_exclusive(0.5, 0.5)) == []  # ask 0.51 + 0.51 > 1


def test_incomplete_dutch_book_is_rejected_below_the_coverage_floor() -> None:
    # PYT-170: two legs of a many-outcome event (asks ~0.04 total) are not the complete exhaustive
    # set — buying YES on both is not an arb. Below the coverage floor it is rejected, even though
    # the sum is < 1; a near-complete set (asks 0.93) is still flagged.
    assert detect_arbitrage(_exclusive(0.013, 0.0075), min_dutch_book_coverage=0.9) == []
    [arb] = detect_arbitrage(_exclusive(0.30, 0.30, 0.30), min_dutch_book_coverage=0.9)
    assert arb.kind is ArbKind.DUTCH_BOOK


def test_dutch_book_coverage_floor_defaults_off() -> None:
    # Default coverage 0.0 keeps the core sum<1 behaviour; the floor is applied at the composition
    # root (run_event_pass) from settings, mirroring the radar min_liquidity pattern.
    [arb] = detect_arbitrage(_exclusive(0.013, 0.0075))
    assert arb.kind is ArbKind.DUTCH_BOOK


def test_ladder_spread_arb_on_a_monotonicity_violation() -> None:
    # Higher threshold (1000) priced above the lower (800) — a monotonicity violation (PYT-138).
    group = _ladder((0.60, 800.0), (0.75, 1000.0))
    [arb] = detect_arbitrage(group)
    assert arb.kind is ArbKind.LADDER_SPREAD
    assert arb.ev == pytest.approx((0.75 - 0.01) - (0.60 + 0.01))  # bid_high - ask_low
    assert {(leg.market_id, leg.side) for leg in arb.legs} == {("0", Side.YES), ("1", Side.NO)}


def test_monotone_ladder_has_no_arb() -> None:
    assert detect_arbitrage(_ladder((0.84, 800.0), (0.70, 1000.0))) == []


def test_unknown_event_has_no_arb() -> None:
    group = EventGroup(event_slug="e", event_type=EventType.UNKNOWN, options=[_opt("0", 0.3)])
    assert detect_arbitrage(group) == []


@given(prices=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=2, max_size=6))
def test_every_detected_arb_has_positive_ev(prices: list[float]) -> None:
    for arb in detect_arbitrage(_exclusive(*prices, spread=0.03)):
        assert arb.ev > 0.0


@given(prices=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=2, max_size=6))
def test_widening_the_spread_never_creates_a_dutch_book(prices: list[float]) -> None:
    # Conservative: if the spread-aware detector flags an arb, a mid-price (spread 0) one does too.
    if detect_arbitrage(_exclusive(*prices, spread=0.05)):
        assert detect_arbitrage(_exclusive(*prices, spread=0.0))


def test_ladder_spread_rejected_above_the_ev_cap() -> None:
    # A 'lock' worth ~0.6 is not real microstructure — almost always a mis-grouped family (PYT-183).
    group = _ladder((0.001, 750.0), (0.61, 1100.0))  # high priced far above low
    assert detect_arbitrage(group, max_ladder_spread_ev=0.10) == []
    [arb] = detect_arbitrage(group)  # default: no cap, still detected
    assert arb.kind is ArbKind.LADDER_SPREAD


@given(
    pt=st.lists(
        st.tuples(st.floats(0.0, 1.0), st.floats(100.0, 2000.0)),
        min_size=2,
        max_size=5,
        unique_by=lambda x: x[1],
    ),
    cap=st.floats(min_value=0.0, max_value=1.0),
)
def test_no_ladder_spread_exceeds_the_ev_cap(pt: list[tuple[float, float]], cap: float) -> None:
    for arb in detect_arbitrage(_ladder(*pt), max_ladder_spread_ev=cap):
        assert arb.ev <= cap


@given(
    pt=st.lists(
        st.tuples(st.floats(0.0, 1.0), st.floats(100.0, 2000.0)),
        min_size=2,
        max_size=5,
        unique_by=lambda x: x[1],
    ),
    valuation=st.floats(0.0, 2100.0),
)
def test_within_family_spread_never_loses_both_legs(
    pt: list[tuple[float, float]], valuation: float
) -> None:
    # A single-family ladder resolves monotonically: market i is YES iff valuation >= threshold_i.
    # The long-YES(low)+short-NO(high) spread can never lose both legs (PYT-183).
    threshold_of = {str(i): t for i, (_, t) in enumerate(pt)}
    for arb in detect_arbitrage(_ladder(*pt)):
        by_side = {leg.side: leg for leg in arb.legs}
        t_low = threshold_of[by_side[Side.YES].market_id]
        t_high = threshold_of[by_side[Side.NO].market_id]
        assert t_low < t_high  # the spread must long the lower threshold and short the higher
        low_leg_wins = valuation >= t_low
        high_leg_wins = valuation < t_high
        assert low_leg_wins or high_leg_wins  # never both lose
