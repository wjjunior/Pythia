"""Venue adapter tests (§4.10): normalization from recorded fixtures, the §8.6 VPN gate, and the
Metaculus access-gated community prediction (null at this token tier ⇒ discarded)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pythia.common import Market, Venue
from pythia.ingestion import LiveVenueAccessError, TransientTransportError
from pythia.ingestion.venues import (
    FixtureTransport,
    VenueClient,
    kalshi,
    manifold,
    metaculus,
    quote_for_market,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "venues"
TS = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)


def test_manifold_normalises_binary_markets() -> None:
    client = VenueClient(
        FixtureTransport(FIXTURES / "manifold_markets.json"), manifold.to_candidate
    )
    candidates = client.candidates()
    assert len(candidates) > 0
    first = candidates[0]
    assert first.external_ref
    assert first.question
    assert first.yes_price is not None
    assert 0.0 <= first.yes_price <= 1.0


def test_kalshi_normalises_active_markets_with_dollar_prices() -> None:
    client = VenueClient(
        FixtureTransport(FIXTURES / "kalshi_markets.json", extract=lambda d: d["markets"]),
        kalshi.to_candidate,
    )
    candidates = client.candidates()
    assert len(candidates) > 0
    for candidate in candidates:
        assert candidate.yes_price is None or 0.0 <= candidate.yes_price <= 1.0


def _kalshi_market(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "active",
        "ticker": "T",
        "title": "q",
        "close_time": "2026-06-22T18:00:00Z",
        "last_price_dollars": 0.0,
        "yes_bid_dollars": 0.0,
        "yes_ask_dollars": 0.0,
    }
    base.update(over)
    return base


def test_kalshi_all_zero_book_is_unpriced() -> None:
    candidate = kalshi.to_candidate(_kalshi_market())
    assert candidate is not None
    assert candidate.yes_price is None  # no trade, empty book ⇒ not a real 0% YES


def test_kalshi_uses_mid_when_book_is_present() -> None:
    candidate = kalshi.to_candidate(_kalshi_market(yes_bid_dollars=0.30, yes_ask_dollars=0.50))
    assert candidate is not None
    assert candidate.yes_price == pytest.approx(0.40)


def test_manifold_discards_resolved_markets() -> None:
    resolved = {
        "id": "m",
        "question": "q",
        "outcomeType": "BINARY",
        "probability": 1.0,
        "isResolved": True,
    }
    assert manifold.to_candidate(resolved) is None


def test_metaculus_real_fixture_has_no_price_at_this_token_tier() -> None:
    client = VenueClient(
        FixtureTransport(FIXTURES / "metaculus_questions.json", extract=lambda d: d["questions"]),
        metaculus.to_candidate,
    )
    candidates = client.candidates()
    assert len(candidates) > 0  # metadata is captured
    assert all(c.yes_price is None for c in candidates)  # CP is access-gated → withheld


def test_metaculus_extracts_documented_community_prediction() -> None:
    documented: dict[str, Any] = {
        "id": 3530,
        "title": "Will X happen by 2027?",
        "scheduled_close_time": "2027-01-01T00:00:00Z",
        "aggregations": {"recency_weighted": {"latest": {"centers": [0.73]}}},
    }
    candidate = metaculus.to_candidate(documented)
    assert candidate is not None
    assert candidate.yes_price == pytest.approx(0.73)


class _GeoBlockedTransport:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self) -> list[dict[str, Any]]:
        self.calls += 1
        raise LiveVenueAccessError("enable the VPN")


def test_venue_client_surfaces_vpn_error_without_retry() -> None:
    transport = _GeoBlockedTransport()
    client = VenueClient(transport, manifold.to_candidate, sleep=lambda _s: None, retries=3)
    with pytest.raises(LiveVenueAccessError):
        client.candidates()
    assert transport.calls == 1


class _FlakyTransport:
    def __init__(self, fail_times: int, payload: list[dict[str, Any]]) -> None:
        self.calls = 0
        self._fail_times = fail_times
        self._payload = payload

    def fetch(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise TransientTransportError("temporary")
        return self._payload


def test_venue_client_retries_transient_errors() -> None:
    payload = [{"id": "m1", "question": "q", "outcomeType": "BINARY", "probability": 0.5}]
    transport = _FlakyTransport(fail_times=2, payload=payload)
    client = VenueClient(transport, manifold.to_candidate, sleep=lambda _s: None, retries=3)
    assert len(client.candidates()) == 1
    assert transport.calls == 3


def test_quote_for_market_matches_and_builds_a_priced_quote() -> None:
    market = Market(
        id="11111111-1111-1111-1111-111111111111",
        external_id="ext",
        question="Will Switzerland win the match on 2026-06-22?",
        category="sports",
        resolution_rule="resolves yes if Switzerland wins",
        close_time=datetime(2026, 6, 22, 18, 0, tzinfo=UTC),
    )
    candidates = [
        manifold.to_candidate(
            {
                "id": "man-1",
                "question": "Will Switzerland win the match on 2026-06-22?",
                "outcomeType": "BINARY",
                "probability": 0.61,
                "closeTime": int(datetime(2026, 6, 22, 18, 0, tzinfo=UTC).timestamp() * 1000),
            }
        )
    ]
    quote = quote_for_market(market, Venue.MANIFOLD, [c for c in candidates if c], TS)
    assert quote is not None
    assert quote.venue == Venue.MANIFOLD
    assert quote.external_ref == "man-1"
    assert quote.yes_price == pytest.approx(0.61)
    assert quote.match_confidence > 0.6


def test_quote_for_market_discards_unpriced_match() -> None:
    market = Market(
        id="11111111-1111-1111-1111-111111111111",
        external_id="ext",
        question="Will inflation exceed 3% in 2026?",
        category="econ",
        resolution_rule="r",
        close_time=datetime(2026, 12, 31, tzinfo=UTC),
    )
    unpriced = metaculus.to_candidate(
        {
            "id": 999,
            "title": "Will inflation exceed 3% in 2026?",
            "scheduled_close_time": "2026-12-31T00:00:00Z",
            "aggregations": {"recency_weighted": {"latest": None}},
        }
    )
    assert unpriced is not None
    assert quote_for_market(market, Venue.METACULUS, [unpriced], TS) is None
