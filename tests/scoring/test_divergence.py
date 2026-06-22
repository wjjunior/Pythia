"""Tests for the pure cross-venue divergence feature (§4.10)."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.common import Venue, VenueQuote
from pythia.scoring.divergence import (
    blended_baseline,
    divergence,
    divergence_penalty,
    latest_prices_per_venue,
)

_prob = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
_T1 = datetime(2026, 1, 1, tzinfo=UTC)
_T2 = datetime(2026, 1, 2, tzinfo=UTC)


def _vq(venue: Venue, ts: datetime, price: float) -> VenueQuote:
    return VenueQuote(
        id="q",
        market_id="m",
        venue=venue,
        external_ref="x",
        ts=ts,
        yes_price=price,
        match_confidence=0.9,
    )


def test_blended_baseline_averages_market_and_venues() -> None:
    assert blended_baseline(0.6, [0.4]) == pytest.approx(0.5)
    assert blended_baseline(0.8, [0.6, 0.4]) == pytest.approx(0.6)


def test_blended_baseline_is_market_q_without_venues() -> None:
    assert blended_baseline(0.73, []) == 0.73


def test_divergence_is_the_spread_across_baselines() -> None:
    assert divergence(0.6, [0.4]) == pytest.approx(0.2)
    assert divergence(0.5, [0.5, 0.5]) == 0.0


def test_no_divergence_without_a_second_baseline() -> None:
    assert divergence(0.6, []) == 0.0


@given(_prob, st.lists(_prob, max_size=5))
def test_divergence_is_a_unit_fraction(market_q: float, venue_prices: list[float]) -> None:
    assert 0.0 <= divergence(market_q, venue_prices) <= 1.0


@given(_prob, st.lists(_prob, min_size=1, max_size=5))
def test_blended_baseline_within_bounds(market_q: float, venue_prices: list[float]) -> None:
    prices = [market_q, *venue_prices]
    blended = blended_baseline(market_q, venue_prices)
    assert min(prices) - 1e-9 <= blended <= max(prices) + 1e-9


def test_divergence_penalty_haircuts_by_the_spread() -> None:
    assert divergence_penalty(0.0) == 1.0
    assert divergence_penalty(0.2) == pytest.approx(0.8)
    assert divergence_penalty(0.5, weight=2.0) == 0.0  # 1 - 2*0.5, clamped to 0


@given(_prob, st.floats(min_value=0.0, max_value=3.0))
def test_divergence_penalty_in_unit_interval(div: float, weight: float) -> None:
    assert 0.0 <= divergence_penalty(div, weight) <= 1.0


def test_latest_prices_per_venue_takes_the_most_recent_per_venue() -> None:
    quotes = [
        _vq(Venue.MANIFOLD, _T1, 0.60),
        _vq(Venue.KALSHI, _T1, 0.55),
        _vq(Venue.MANIFOLD, _T2, 0.64),  # newer Manifold quote wins
    ]
    assert sorted(latest_prices_per_venue(quotes)) == [0.55, 0.64]


def test_latest_prices_per_venue_is_empty_without_quotes() -> None:
    assert latest_prices_per_venue([]) == []
