"""Unit tests for event-level grouping & classification (PYT-137)."""

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from pythia.common import EventType, Market, MarketSnapshot
from pythia.scoring.events import (
    LadderDirection,
    build_event_group,
    classify_event,
    detect_arbitrage,
    option_signature,
    parse_threshold,
    threshold_direction,
)

ASOF = datetime(2026, 3, 1, tzinfo=UTC)


def _market(mid: str, question: str, *, neg_risk: bool = False) -> Market:
    return Market(
        id=mid,
        external_id="0x" + mid,
        question=question,
        category="x",
        resolution_rule="r",
        neg_risk=neg_risk,
    )


def _snap(mid: str, yes: float, spread: float = 0.02) -> MarketSnapshot:
    return MarketSnapshot(
        id="s" + mid,
        market_id=mid,
        ts=ASOF,
        yes_price=yes,
        spread=spread,
        volume=1.0,
        liquidity=1.0,
    )


def test_parse_threshold_reads_scaled_dollar_amounts() -> None:
    assert parse_threshold("OpenAI IPO cap above $800 billion?") == 800e9
    assert parse_threshold("... above $1 trillion?") == 1e12
    assert parse_threshold("... above $1.2 trillion?") == 1.2e12
    assert parse_threshold("... above $1,400,000?") == 1_400_000.0


def test_parse_threshold_returns_none_without_a_dollar_amount() -> None:
    assert parse_threshold("Will Anthropic have the best model?") is None


def test_negrisk_event_is_mutually_exclusive() -> None:
    markets = [
        _market("a", "Will Anthropic win?", neg_risk=True),
        _market("b", "Will OpenAI win?", neg_risk=True),
    ]
    assert classify_event(markets) is EventType.MUTUALLY_EXCLUSIVE


def test_threshold_ladder_detected_without_negrisk() -> None:
    markets = [
        _market("a", "IPO cap above $800 billion?"),
        _market("b", "IPO cap above $1 trillion?"),
    ]
    assert classify_event(markets) is EventType.THRESHOLD_LADDER


def test_non_negrisk_non_threshold_is_unknown() -> None:
    markets = [_market("a", "Will it rain?"), _market("b", "Will it snow?")]
    assert classify_event(markets) is EventType.UNKNOWN


def test_threshold_direction_reads_above_and_below() -> None:
    assert threshold_direction("Will Bitcoin reach $61,000?") is LadderDirection.ABOVE
    assert threshold_direction("Will revenue exceed $5 billion?") is LadderDirection.ABOVE
    assert threshold_direction("IPO cap above $800 billion?") is LadderDirection.ABOVE
    assert threshold_direction("Will Bitcoin dip to $57,000?") is LadderDirection.BELOW
    assert threshold_direction("Will the price drop to $50?") is LadderDirection.BELOW


def test_threshold_direction_is_none_when_ambiguous() -> None:
    assert threshold_direction("Will the cap be $5 billion?") is None  # neither keyword
    assert threshold_direction("Will it go above $5 or drop to $4?") is None  # both directions


def test_mixed_direction_threshold_event_is_unknown() -> None:
    # PYT-149: "dip to $X" + "reach $X" is a pivot, not a monotone ladder.
    markets = [
        _market("a", "Will Bitcoin dip to $57,000 on June 27?"),
        _market("b", "Will Bitcoin reach $61,000 on June 27?"),
    ]
    assert classify_event(markets) is EventType.UNKNOWN


def test_below_type_threshold_event_is_unknown() -> None:
    markets = [
        _market("a", "Will Bitcoin dip to $57,000?"),
        _market("b", "Will Bitcoin dip to $58,000?"),
    ]
    assert classify_event(markets) is EventType.UNKNOWN


def test_no_ladder_arb_on_a_mixed_direction_event() -> None:
    # The bitcoin "dip to / reach" pivot must yield no ladder_spread (the PYT-149 live false
    # positive: the old classifier read these prices as a monotonicity violation worth ev 0.01).
    options = [
        (_market("lo", "Will Bitcoin dip to $57,000?"), _snap("lo", 0.004)),
        (_market("hi", "Will Bitcoin reach $61,000?"), _snap("hi", 0.034)),
    ]
    group = build_event_group("btc", options)
    assert group.event_type is EventType.UNKNOWN
    assert detect_arbitrage(group) == []


def test_single_option_negrisk_event_is_not_mutually_exclusive() -> None:
    # A coupled set needs >=2 options; a lone negRisk market is degenerate (Copilot review).
    assert classify_event([_market("a", "Will X win?", neg_risk=True)]) is EventType.UNKNOWN


def test_inconsistent_negrisk_across_options_is_not_mutually_exclusive() -> None:
    # negRisk is event-level — require *all* options to carry it, not just one.
    markets = [_market("a", "Will X win?", neg_risk=True), _market("b", "Will Y win?")]
    assert classify_event(markets) is EventType.UNKNOWN


def test_build_event_group_orders_ladder_options_by_threshold() -> None:
    options = [
        (_market("hi", "cap above $1 trillion?"), _snap("hi", 0.70)),
        (_market("lo", "cap above $800 billion?"), _snap("lo", 0.84)),
    ]
    group = build_event_group("openai-ipo", options)
    assert group.event_type is EventType.THRESHOLD_LADDER
    assert [o.threshold for o in group.options] == [800e9, 1e12]  # ascending threshold
    assert [o.yes_price for o in group.options] == [0.84, 0.70]


def test_build_event_group_mutually_exclusive_has_no_thresholds() -> None:
    options = [
        (_market("a", "Will Anthropic win?", neg_risk=True), _snap("a", 0.99)),
        (_market("b", "Will OpenAI win?", neg_risk=True), _snap("b", 0.004)),
    ]
    group = build_event_group("ai-model", options)
    assert group.event_type is EventType.MUTUALLY_EXCLUSIVE
    assert all(o.threshold is None for o in group.options)


@given(st.lists(st.tuples(st.text(min_size=1, max_size=20), st.booleans()), max_size=6))
def test_classify_event_is_independent_of_option_order(items: list[tuple[str, bool]]) -> None:
    markets = [_market(str(i), q, neg_risk=nr) for i, (q, nr) in enumerate(items)]
    assert classify_event(markets) == classify_event(list(reversed(markets)))


def test_option_signature_collapses_thresholds_within_a_family() -> None:
    lo1 = option_signature("Will Anthropic's valuation hit (LOW) $750B by June 30?")
    lo2 = option_signature("Will Anthropic's valuation hit (LOW) $800B by June 30?")
    hi = option_signature("Will Anthropic's valuation hit (HIGH) $1.1T by June 30?")
    assert lo1 == lo2  # same family — only the threshold differs, and it is collapsed
    assert lo1 != hi  # (LOW) and (HIGH) are distinct families


def test_option_signature_normalizes_case_and_whitespace() -> None:
    assert option_signature("IPO cap  above  $800 billion?") == option_signature(
        "ipo cap above $1 trillion?"
    )
