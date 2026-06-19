"""Manifold adapter (§4.10): open API, binary markets carry `probability` as the YES price."""

from datetime import UTC, datetime
from typing import Any

from pythia.common import Venue
from pythia.ingestion.venues.base import VenueCandidate, fetch_json

VENUE = Venue.MANIFOLD
MARKETS_URL = "https://api.manifold.markets/v0/markets"


def to_candidate(raw: dict[str, Any]) -> VenueCandidate | None:
    if raw.get("outcomeType") != "BINARY" or raw.get("probability") is None:
        return None
    if raw.get("isResolved"):
        return None  # a settled price would poison divergence against an open market
    close = raw.get("closeTime")
    return VenueCandidate(
        external_ref=str(raw["id"]),
        question=str(raw["question"]),
        close_time=datetime.fromtimestamp(close / 1000, tz=UTC) if close else None,
        yes_price=float(raw["probability"]),
    )


class ManifoldHttpTransport:
    def __init__(self, *, limit: int = 100) -> None:
        self._limit = limit

    def fetch(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = fetch_json(
            MARKETS_URL, venue=VENUE.value, params={"limit": self._limit}
        )
        return data
