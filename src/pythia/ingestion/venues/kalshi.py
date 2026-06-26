"""Kalshi adapter (§4.10, §8.6): US-regulated and geo-restricted — live access needs the VPN.

Binary markets quote prices in dollars (0 to 1). YES price is the last trade, falling back to the
bid/ask mid when there is no last trade.
"""

from datetime import datetime
from typing import Any

from pythia.common import Venue
from pythia.ingestion.venues.base import VenueCandidate, fetch_json

VENUE = Venue.KALSHI
MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"


def _yes_price(raw: dict[str, Any]) -> float | None:
    last = raw.get("last_price_dollars")
    if last is not None and float(last) != 0.0:
        return float(last)
    bid = raw.get("yes_bid_dollars")
    ask = raw.get("yes_ask_dollars")
    if bid is not None and ask is not None and (float(bid) != 0.0 or float(ask) != 0.0):
        return (float(bid) + float(ask)) / 2.0
    return None  # no trade and no book ⇒ unpriced, not a real 0% YES


def to_candidate(raw: dict[str, Any]) -> VenueCandidate | None:
    if raw.get("status") != "active":
        return None
    close = raw.get("close_time")
    return VenueCandidate(
        external_ref=str(raw["ticker"]),
        question=str(raw["title"]),
        close_time=datetime.fromisoformat(close) if close else None,
        yes_price=_yes_price(raw),
    )


class KalshiHttpTransport:
    def __init__(self, *, use_live: bool = False, limit: int = 100) -> None:
        self._use_live = use_live
        self._limit = limit

    def fetch(self) -> list[dict[str, Any]]:
        payload = fetch_json(
            MARKETS_URL,
            venue=VENUE.value,
            use_live=self._use_live,
            params={"limit": self._limit, "status": "open"},
        )
        markets: list[dict[str, Any]] = payload["markets"]
        return markets
