"""Cross-venue divergence (§4.10, §3.11): a calibration signal, never an arbitrage trigger.

Divergence between our market and independently-formed venue baselines flags consensus fragility —
a wide spread means the venues disagree on the event, so the edge is less trustworthy. Pure: the
prices are passed in; retrieval (asof-filtered) and venue I/O live in the adapter layer.
"""

from pythia.common.venue import blended_baseline as blended_baseline
from pythia.common.venue import latest_prices_per_venue as latest_prices_per_venue


def divergence_penalty(divergence: float, weight: float = 1.0) -> float:
    return min(max(1.0 - weight * divergence, 0.0), 1.0)


def divergence(market_q: float, venue_prices: list[float]) -> float:
    if not venue_prices:
        return 0.0
    prices = [market_q, *venue_prices]
    return max(prices) - min(prices)
