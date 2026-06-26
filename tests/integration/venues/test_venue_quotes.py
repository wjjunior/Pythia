"""venue_quotes on real Postgres (§4.10): append-only ingest + asof point-in-time retrieval."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from pythia.common import Market, Venue, VenueQuote
from pythia.db.repositories import MarketRepository, VenueQuoteRepository
from pythia.ingestion.venues import FixtureTransport, VenueClient, manifold, quote_for_market

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "venues"
T1 = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)
T2 = T1 + timedelta(hours=1)


def _market(session: Session, question: str, *, close: datetime | None) -> Market:
    return MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "venue-market")),
            external_id="venue-ext",
            question=question,
            category="sports",
            resolution_rule="r",
            close_time=close,
        )
    )


def _quote(market_id: str, venue: Venue, ref: str, ts: datetime, price: float) -> VenueQuote:
    return VenueQuote(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{venue.value}:{ref}@{ts.isoformat()}")),
        market_id=market_id,
        venue=venue,
        external_ref=ref,
        ts=ts,
        yes_price=price,
        match_confidence=0.8,
    )


def test_retrieve_is_asof_filtered_point_in_time(session: Session) -> None:
    market = _market(session, "Will it resolve yes?", close=None)
    repo = VenueQuoteRepository(session)
    repo.add(_quote(market.id, Venue.MANIFOLD, "m1", T1, 0.61))
    repo.add(_quote(market.id, Venue.KALSHI, "k1", T1, 0.58))
    repo.add(_quote(market.id, Venue.MANIFOLD, "m2", T2, 0.64))

    at_t1 = repo.retrieve(market.id, asof=T1)
    assert {q.external_ref for q in at_t1} == {"m1", "k1"}  # the T2 quote is in the future
    assert all(q.ts <= T1 for q in at_t1)

    at_t2 = repo.retrieve(market.id, asof=T2)
    assert {q.external_ref for q in at_t2} == {"m1", "k1", "m2"}


def test_should_filter_retrieve_for_markets_by_asof(session: Session) -> None:
    market = _market(session, "Will it resolve yes?", close=None)
    repo = VenueQuoteRepository(session)
    repo.add(_quote(market.id, Venue.MANIFOLD, "m1", T1, 0.61))
    repo.add(_quote(market.id, Venue.MANIFOLD, "m2", T2, 0.64))

    by_market = repo.retrieve_for_markets({market.id: T1})
    refs = {q.external_ref for q in by_market.get(market.id, [])}
    assert refs == {"m1"}  # the T2 quote is excluded in SQL by the per-market asof


def test_quote_round_trips_venue_and_fields(session: Session) -> None:
    market = _market(session, "Will it resolve yes?", close=None)
    repo = VenueQuoteRepository(session)
    repo.add(_quote(market.id, Venue.METACULUS, "q1", T1, 0.42))
    stored = repo.retrieve(market.id, asof=T2)[0]
    assert stored.venue == Venue.METACULUS
    assert stored.yes_price == pytest.approx(0.42)
    assert stored.match_confidence == pytest.approx(0.8)


def test_end_to_end_from_fixture_match_to_storage(session: Session) -> None:
    client = VenueClient(
        FixtureTransport(FIXTURES / "manifold_markets.json"), manifold.to_candidate
    )
    candidates = client.candidates()
    target = candidates[0]
    market = _market(session, target.question, close=target.close_time)

    quote = quote_for_market(market, Venue.MANIFOLD, candidates, T1)
    assert quote is not None
    assert quote.external_ref == target.external_ref

    repo = VenueQuoteRepository(session)
    repo.add(quote)
    retrieved = repo.retrieve(market.id, asof=T1)
    assert len(retrieved) == 1
    assert retrieved[0].yes_price == pytest.approx(target.yes_price)
    assert retrieved[0].match_confidence > 0.6
