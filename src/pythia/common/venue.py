"""Pure helpers for cross-venue price aggregation (§4.10). No I/O, no side effects."""

from collections.abc import Iterable

from pythia.common.types import VenueQuote


def latest_prices_per_venue(quotes: Iterable[VenueQuote]) -> list[float]:
    """Most-recent YES price per venue — eliminates stale observations before aggregating."""
    latest: dict[str, VenueQuote] = {}
    for quote in quotes:
        current = latest.get(quote.venue)
        if current is None or quote.ts > current.ts:
            latest[quote.venue] = quote
    return [quote.yes_price for quote in latest.values()]


def blended_baseline(market_q: float, venue_prices: list[float]) -> float:
    """Simple average of market q and all venue prices — the cross-venue Brier baseline."""
    if not venue_prices:
        return market_q
    return (market_q + sum(venue_prices)) / (1 + len(venue_prices))
