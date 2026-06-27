"""Event-level grouping & classification of multi-option markets (PYT-137).

A Polymarket multi-option event is a group of binary Yes/No markets sharing an ``event_slug``, of
two structural types: a mutually-exclusive set (negRisk — exactly one option resolves YES) or a
monotonic threshold ladder (nested "above $X"). Pure: classification and assembly take stored data
as input — no I/O, no clock.
"""

import re
from collections.abc import Sequence

from pythia.common import (
    ArbKind,
    ArbLeg,
    ArbOpportunity,
    EventGroup,
    EventOption,
    EventType,
    Market,
    MarketSnapshot,
    Side,
)

_SCALE = {
    "thousand": 1e3,
    "k": 1e3,
    "million": 1e6,
    "m": 1e6,
    "billion": 1e9,
    "b": 1e9,
    "trillion": 1e12,
    "t": 1e12,
}
_THRESHOLD_RE = re.compile(
    r"\$\s*([0-9][0-9,]*\.?[0-9]*)\s*(thousand|million|billion|trillion|k|m|b|t)?\b",
    re.IGNORECASE,
)


def parse_threshold(question: str) -> float | None:
    """Best-effort numeric threshold from a '... above $X billion' question; None if absent."""
    match = _THRESHOLD_RE.search(question)
    if match is None:
        return None
    amount = float(match.group(1).replace(",", ""))
    return amount * _SCALE.get((match.group(2) or "").lower(), 1.0)


def classify_event(markets: Sequence[Market]) -> EventType:
    """all-negRisk (>=2) ⇒ exclusive; >=2 distinct parseable $ thresholds ⇒ ladder; else unknown."""
    if len(markets) < 2:
        return EventType.UNKNOWN  # a single option is not a coupled set
    if all(market.neg_risk for market in markets):
        return EventType.MUTUALLY_EXCLUSIVE  # negRisk is event-level — every option carries it
    distinct = {t for t in (parse_threshold(m.question) for m in markets) if t is not None}
    if len(distinct) == len(markets):
        return EventType.THRESHOLD_LADDER
    return EventType.UNKNOWN


def build_event_group(
    event_slug: str, options: Sequence[tuple[Market, MarketSnapshot]]
) -> EventGroup:
    """Assemble a classified, ordered EventGroup from each option's market + its latest snapshot."""
    event_type = classify_event([market for market, _ in options])
    is_ladder = event_type is EventType.THRESHOLD_LADDER
    event_options = [
        EventOption(
            market_id=market.id,
            question=market.question,
            yes_price=snapshot.yes_price,
            spread=snapshot.spread,
            threshold=parse_threshold(market.question) if is_ladder else None,
        )
        for market, snapshot in options
    ]
    if is_ladder:
        event_options.sort(key=lambda option: option.threshold or 0.0)
    return EventGroup(event_slug=event_slug, event_type=event_type, options=event_options)


def _ask(option: EventOption) -> float:
    return min(1.0, option.yes_price + option.spread / 2.0)


def _bid(option: EventOption) -> float:
    return max(0.0, option.yes_price - option.spread / 2.0)


def detect_arbitrage(group: EventGroup) -> list[ArbOpportunity]:
    """Locked arbitrage in an event, priced conservatively at the spread-adjusted ask/bid."""
    if group.event_type is EventType.MUTUALLY_EXCLUSIVE:
        return _dutch_book(group)
    if group.event_type is EventType.THRESHOLD_LADDER:
        return _ladder_spreads(group)
    return []


def _dutch_book(group: EventGroup) -> list[ArbOpportunity]:
    # negRisk ⇒ exactly one option resolves YES, so buying YES on all costs sum(ask) and pays $1.
    total_ask = sum(_ask(option) for option in group.options)
    if total_ask >= 1.0:
        return []
    legs = [ArbLeg(market_id=o.market_id, side=Side.YES, price=_ask(o)) for o in group.options]
    return [
        ArbOpportunity(
            event_slug=group.event_slug, kind=ArbKind.DUTCH_BOOK, legs=legs, ev=1.0 - total_ask
        )
    ]


def _ladder_spreads(group: EventGroup) -> list[ArbOpportunity]:
    # Ascending by threshold: YES(>t_low) dominates YES(>t_high), so fair price_low >= price_high.
    # A violation prices high above low; the locked arb is long YES(low) + short YES(high) via NO,
    # with ev = bid(high) - ask(low) (worst-case payoff 1, cost ask_low + 1 - bid_high).
    options = group.options
    arbs: list[ArbOpportunity] = []
    for i in range(len(options)):
        for j in range(i + 1, len(options)):
            low, high = options[i], options[j]
            ev = _bid(high) - _ask(low)
            if ev <= 0.0:
                continue
            legs = [
                ArbLeg(market_id=low.market_id, side=Side.YES, price=_ask(low)),
                ArbLeg(market_id=high.market_id, side=Side.NO, price=1.0 - _bid(high)),
            ]
            arbs.append(
                ArbOpportunity(
                    event_slug=group.event_slug, kind=ArbKind.LADDER_SPREAD, legs=legs, ev=ev
                )
            )
    return arbs
