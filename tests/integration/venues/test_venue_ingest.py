"""venue_ingest_tick on real Postgres (§4.10, PYT-38): match → persist, duplicate safety."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from pythia.common import Market, Venue
from pythia.db.repositories import MarketRepository, VenueQuoteRepository
from pythia.ingestion.venues import FixtureTransport, VenueClient, manifold
from pythia.worker.venue_ingest import ingest_tick

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "venues"
T1 = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)

MANIFOLD_QUESTION = "Does China have an ASML EUV machine? (as of June 19, 2026)"


def _market(session: Session) -> Market:
    return MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "ingest-tick-market")),
            external_id="ingest-tick-ext",
            question=MANIFOLD_QUESTION,
            category="geopolitics",
            resolution_rule="r",
        )
    )


def _manifold_client() -> tuple[Venue, VenueClient]:
    return Venue.MANIFOLD, VenueClient(
        FixtureTransport(FIXTURES / "manifold_markets.json"), manifold.to_candidate
    )


def test_ingest_tick_persists_matched_quote(session: Session) -> None:
    market = _market(session)
    venue_enum, client = _manifold_client()
    count = ingest_tick(session, [(venue_enum, client)], ts=T1)
    assert count == 1
    quotes = VenueQuoteRepository(session).retrieve(market.id, asof=T1)
    assert len(quotes) == 1
    assert quotes[0].venue == Venue.MANIFOLD
    assert quotes[0].yes_price == pytest.approx(0.259, abs=1e-3)


def test_ingest_tick_returns_zero_for_empty_clients(session: Session) -> None:
    _market(session)
    assert ingest_tick(session, [], ts=T1) == 0


def test_ingest_tick_skips_duplicate_gracefully(session: Session) -> None:
    _market(session)
    venue_enum, client = _manifold_client()
    first = ingest_tick(session, [(venue_enum, client)], ts=T1)
    second = ingest_tick(session, [(venue_enum, client)], ts=T1)
    assert first == 1
    assert second == 0
