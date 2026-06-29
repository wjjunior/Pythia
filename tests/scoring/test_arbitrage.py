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
    return EventGroup(
        event_slug="e",
        event_type=EventType.THRESHOLD_LADDER,
        options=[_opt(str(i), p, threshold=t) for i, (p, t) in enumerate(price_threshold)],
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
