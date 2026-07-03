"""Event-level grouping & classification of multi-option markets (PYT-137).

A Polymarket multi-option event is a group of binary Yes/No markets sharing an ``event_slug``, of
two structural types: a mutually-exclusive set (negRisk — exactly one option resolves YES) or a
monotonic threshold ladder (nested "above $X"). Pure: classification and assembly take stored data
as input — no I/O, no clock.
"""

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum

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


_THRESHOLD_PLACEHOLDER = "$#"


def option_signature(question: str) -> str:
    """A ladder family key: the question with every $-threshold collapsed to a placeholder and
    case/whitespace normalized. Two options share a ladder only if their signatures match, so
    '(LOW) $750B' and '(HIGH) $1.1T' never pair (PYT-183)."""
    collapsed = _THRESHOLD_RE.sub(_THRESHOLD_PLACEHOLDER, question)
    return " ".join(collapsed.lower().split())


class LadderDirection(StrEnum):
    """Which side of the threshold a question asks about — sets a ladder's monotonicity."""

    ABOVE = "above"  # "reach/exceed $X": P is non-increasing in the threshold
    BELOW = "below"  # "dip to $X": P is non-decreasing in the threshold


_ABOVE_RE = re.compile(
    r"\b(above|over|exceeds?|reach(es|ed)?|surpass(es|ed)?|hits?|at least|or higher|or more)\b",
    re.IGNORECASE,
)
_BELOW_RE = re.compile(
    r"\b(below|under|at most|or lower|or less)\b|\b(dips?|drops?|falls?) to\b",
    re.IGNORECASE,
)


def threshold_direction(question: str) -> LadderDirection | None:
    """ABOVE for 'reach/exceed $X', BELOW for 'dip to $X'; None if ambiguous (neither or both)."""
    above = bool(_ABOVE_RE.search(question))
    below = bool(_BELOW_RE.search(question))
    if above and not below:
        return LadderDirection.ABOVE
    if below and not above:
        return LadderDirection.BELOW
    return None


def classify_event(markets: Sequence[Market]) -> EventType:
    """all-negRisk ⇒ exclusive; distinct $ thresholds, all 'above'-type ⇒ ladder; else unknown."""
    if len(markets) < 2:
        return EventType.UNKNOWN  # a single option is not a coupled set
    if all(market.neg_risk for market in markets):
        return EventType.MUTUALLY_EXCLUSIVE  # negRisk is event-level — every option carries it
    thresholds = [parse_threshold(m.question) for m in markets]
    directions = [threshold_direction(m.question) for m in markets]
    # A ladder must be monotone in one direction; only above-type satisfies _ladder_spreads /
    # isotonic. A below-type or mixed ("dip to" + "reach") event is not a ladder (PYT-149).
    if (
        all(t is not None for t in thresholds)
        and len(set(thresholds)) == len(markets)
        and all(d is LadderDirection.ABOVE for d in directions)
    ):
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


def build_event_groups(
    event_slug: str, options: Sequence[tuple[Market, MarketSnapshot]]
) -> list[EventGroup]:
    """One coherent coupled set per group: a negRisk event stays whole (mutually exclusive); a
    non-negRisk event is partitioned by option_signature so each threshold family is its own ladder
    and cross-family spreads can never form (PYT-183)."""
    if len(options) >= 2 and all(market.neg_risk for market, _ in options):
        return [build_event_group(event_slug, options)]
    families: dict[str, list[tuple[Market, MarketSnapshot]]] = {}
    for market, snapshot in options:
        families.setdefault(option_signature(market.question), []).append((market, snapshot))
    return [build_event_group(event_slug, family) for family in families.values()]


def _ask(option: EventOption) -> float:
    return min(1.0, option.yes_price + option.spread / 2.0)


def _bid(option: EventOption) -> float:
    return max(0.0, option.yes_price - option.spread / 2.0)


def detect_arbitrage(
    group: EventGroup, *, min_dutch_book_coverage: float = 0.0
) -> list[ArbOpportunity]:
    """Locked arbitrage in an event, priced conservatively at the spread-adjusted ask/bid."""
    if group.event_type is EventType.MUTUALLY_EXCLUSIVE:
        return _dutch_book(group, min_coverage=min_dutch_book_coverage)
    if group.event_type is EventType.THRESHOLD_LADDER:
        return _ladder_spreads(group)
    return []


def _dutch_book(group: EventGroup, *, min_coverage: float) -> list[ArbOpportunity]:
    # negRisk ⇒ exactly one option resolves YES, so buying YES on all costs sum(ask) and pays $1 —
    # but only when the options are the COMPLETE exhaustive set. All-negRisk legs are not proof we
    # hold them all (PYT-170): a complete set prices to ~1, so a total_ask far below 1 means most of
    # the probability mass is missing (e.g. 2 of ~30 exact scores). Buying YES on that fraction is
    # not an arb — an unheld outcome would resolve and every leg loses. min_coverage rejects it.
    total_ask = sum(_ask(option) for option in group.options)
    if not (min_coverage <= total_ask < 1.0):
        return []
    legs = [
        ArbLeg(market_id=o.market_id, side=Side.YES, price=_ask(o), question=o.question)
        for o in group.options
    ]
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
                ArbLeg(
                    market_id=low.market_id, side=Side.YES, price=_ask(low), question=low.question
                ),
                ArbLeg(
                    market_id=high.market_id,
                    side=Side.NO,
                    price=1.0 - _bid(high),
                    question=high.question,
                ),
            ]
            arbs.append(
                ArbOpportunity(
                    event_slug=group.event_slug, kind=ArbKind.LADDER_SPREAD, legs=legs, ev=ev
                )
            )
    return arbs


def _project_simplex(values: Sequence[float]) -> list[float]:
    """Euclidean projection onto the probability simplex {x >= 0, sum x = 1} (Duchi et al.)."""
    descending = sorted(values, reverse=True)
    cumulative = 0.0
    theta = 0.0
    for index, value in enumerate(descending):
        cumulative += value
        candidate = (cumulative - 1.0) / (index + 1)
        if value - candidate > 0.0:
            theta = candidate
    return [max(0.0, value - theta) for value in values]


def _isotonic_decreasing(values: Sequence[float]) -> list[float]:
    """L2 projection onto non-increasing sequences (pool-adjacent-violators)."""
    blocks: list[tuple[float, int]] = []  # (sum, count) per pooled block
    for value in values:
        total, count = value, 1
        while blocks and total / count > blocks[-1][0] / blocks[-1][1]:
            prev_total, prev_count = blocks.pop()
            total += prev_total
            count += prev_count
        blocks.append((total, count))
    projected: list[float] = []
    for total, count in blocks:
        projected.extend([total / count] * count)
    return projected


def couple_probabilities(group: EventGroup, p_by_market: Mapping[str, float]) -> dict[str, float]:
    """Project per-option committed probabilities onto the event's structurally-valid set (PYT-139).

    Mutually-exclusive ⇒ L2 projection onto the simplex (Σ=1); threshold ladder ⇒ isotonic (non-
    increasing in the threshold). Both are non-expansive, so the projection can only sharpen Brier.
    """
    market_ids = [option.market_id for option in group.options]
    if len(market_ids) < 2 or not all(market_id in p_by_market for market_id in market_ids):
        return dict(p_by_market)
    probabilities = [p_by_market[market_id] for market_id in market_ids]  # group.options order
    if group.event_type is EventType.MUTUALLY_EXCLUSIVE:
        adjusted = _project_simplex(probabilities)
    elif group.event_type is EventType.THRESHOLD_LADDER:
        adjusted = _isotonic_decreasing(probabilities)
    else:
        return dict(p_by_market)
    return {**p_by_market, **dict(zip(market_ids, adjusted, strict=True))}
