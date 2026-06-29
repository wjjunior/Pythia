"""Calibration-priority helpers (PYT-75): bet side, entry price, win multiple, days-to-close."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.common import Side
from pythia.scoring.prioritization import (
    closes_in_days,
    entry_price,
    price_band_priority,
    radar_metrics,
    recommended_side,
    win_multiple,
    win_probability,
)

_PROB = st.floats(min_value=0.01, max_value=0.99)
_ASOF = datetime(2026, 6, 23, tzinfo=UTC)


def test_win_probability_is_the_model_probability_of_the_bet_side() -> None:
    assert win_probability(Side.YES, 0.7) == 0.7
    assert win_probability(Side.NO, 0.7) == pytest.approx(0.3)


def test_radar_metrics_carries_win_probability_of_the_recommended_side() -> None:
    # p_model 0.01, q 0.0005 -> recommended YES (edge), but the model gives it only 1% (a longshot)
    metrics = radar_metrics(p_model=0.01, q=0.0005, close_time=None, resolves_at=None, asof=_ASOF)
    assert metrics.recommended_side is Side.YES
    assert metrics.win_probability == pytest.approx(0.01)


def test_recommended_side_follows_the_edge_sign() -> None:
    assert recommended_side(0.7, 0.5) is Side.YES
    assert recommended_side(0.3, 0.5) is Side.NO
    assert recommended_side(0.5, 0.5) is Side.YES


def test_entry_price_is_the_cost_of_the_bet_side() -> None:
    assert entry_price(Side.YES, 0.4) == 0.4
    assert entry_price(Side.NO, 0.4) == 0.6


def test_win_multiple_is_the_payout_per_unit_cost() -> None:
    assert win_multiple(0.5) == 2.0
    assert win_multiple(0.25) == 4.0
    assert win_multiple(1.0 / 1.4) == pytest.approx(1.4)


def test_win_multiple_stays_finite_for_a_degenerate_price() -> None:
    import math

    assert math.isfinite(win_multiple(0.0))


def test_closes_in_days_measures_from_asof() -> None:
    assert closes_in_days(None, _ASOF) is None
    assert closes_in_days(_ASOF + timedelta(days=2), _ASOF) == 2.0


def test_radar_metrics_bundles_the_display_fields() -> None:
    metrics = radar_metrics(
        p_model=0.2,
        q=0.55,
        close_time=_ASOF + timedelta(days=3),
        resolves_at=_ASOF + timedelta(days=3),
        asof=_ASOF,
    )
    assert metrics.recommended_side is Side.NO
    assert metrics.entry_price == pytest.approx(0.45)
    assert metrics.win_multiple == pytest.approx(1.0 / 0.45)
    assert metrics.closes_in_days == 3.0
    assert metrics.resolves_in_days == 3.0


def test_resolves_in_days_uses_resolution_date_not_trading_close() -> None:
    # The OpenAI IPO case: trades close in 7d but resolution is ~6 months out.
    metrics = radar_metrics(
        p_model=0.2,
        q=0.05,
        close_time=_ASOF + timedelta(days=7),
        resolves_at=_ASOF + timedelta(days=191),
        asof=_ASOF,
    )
    assert metrics.closes_in_days == pytest.approx(7.0)
    assert metrics.resolves_in_days == pytest.approx(191.0)


def test_resolves_in_days_falls_back_to_close_when_resolution_absent() -> None:
    metrics = radar_metrics(
        p_model=0.2,
        q=0.05,
        close_time=_ASOF + timedelta(days=4),
        resolves_at=None,
        asof=_ASOF,
    )
    assert metrics.resolves_in_days == pytest.approx(4.0)


@given(st.floats(min_value=0.01, max_value=1.0))
def test_win_multiple_is_at_least_one_for_any_valid_price(entry: float) -> None:
    assert win_multiple(entry) >= 1.0


@given(_PROB, _PROB)
def test_recommended_side_picks_the_underpriced_side(p: float, q: float) -> None:
    if recommended_side(p, q) is Side.YES:
        assert p >= q
    else:
        assert p < q


_PRICE = st.floats(min_value=0.0, max_value=1.0)


@given(_PRICE, _PRICE, _PRICE)
def test_price_band_priority_is_midband_iff_within_bounds(a: float, b: float, price: float) -> None:
    lo, hi = sorted((a, b))  # PYT-167: midband (rank 0) exactly when lo <= price <= hi
    rank = price_band_priority(price, lo=lo, hi=hi)
    assert rank in (0, 1)
    assert (rank == 0) == (lo <= price <= hi)
