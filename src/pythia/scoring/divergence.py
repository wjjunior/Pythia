"""Cross-venue divergence (§4.10, §3.11): a calibration signal, never an arbitrage trigger.

Divergence between our market and independently-formed venue baselines flags consensus fragility —
a wide spread means the venues disagree on the event, so the edge is less trustworthy. Pure: the
prices are passed in; retrieval (asof-filtered) and venue I/O live in the adapter layer.
"""


def blended_baseline(market_q: float, venue_prices: list[float]) -> float:
    if not venue_prices:
        return market_q
    return (market_q + sum(venue_prices)) / (1 + len(venue_prices))


def divergence(market_q: float, venue_prices: list[float]) -> float:
    if not venue_prices:
        return 0.0
    prices = [market_q, *venue_prices]
    return max(prices) - min(prices)
