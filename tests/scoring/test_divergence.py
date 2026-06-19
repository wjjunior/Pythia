"""Tests for the pure cross-venue divergence feature (§4.10)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.scoring.divergence import blended_baseline, divergence

_prob = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


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
