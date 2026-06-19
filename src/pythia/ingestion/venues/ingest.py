"""Compose matcher + venue candidates into a stored VenueQuote (§4.10). No execution path."""

import uuid
from datetime import datetime

from pythia.common import Market, Venue, VenueQuote
from pythia.ingestion.venue_match import MatchCandidate, best_match
from pythia.ingestion.venues.base import VenueCandidate


def _quote_id(venue: Venue, external_ref: str, ts: datetime) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{venue.value}:{external_ref}@{ts.isoformat()}"))


def quote_for_market(
    market: Market, venue: Venue, candidates: list[VenueCandidate], ts: datetime
) -> VenueQuote | None:
    ours = MatchCandidate(market.question, market.close_time)
    match = best_match(
        ours,
        [
            (candidate, MatchCandidate(candidate.question, candidate.close_time))
            for candidate in candidates
        ],
    )
    if match is None:
        return None
    candidate, confidence = match
    if candidate.yes_price is None:
        return None
    return VenueQuote(
        id=_quote_id(venue, candidate.external_ref, ts),
        market_id=market.id,
        venue=venue,
        external_ref=candidate.external_ref,
        ts=ts,
        yes_price=candidate.yes_price,
        match_confidence=confidence,
    )
