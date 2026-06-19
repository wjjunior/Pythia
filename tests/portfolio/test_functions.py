"""Property tests for the pure capital-state math (§4.4.1)."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.portfolio import OpenPosition, available_capital, exposure_fraction, exposure_state

_money = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_DAY = timedelta(days=1)
_WEEK = timedelta(days=7)


def test_available_capital_is_bankroll_plus_pnl_minus_locked() -> None:
    assert available_capital(10_000.0, 250.0, 400.0) == 9_850.0


@given(_money, st.floats(min_value=-1e9, max_value=1e9, allow_nan=False), _money)
def test_available_capital_identity(bankroll: float, realized: float, locked: float) -> None:
    assert available_capital(bankroll, realized, locked) == bankroll + realized - locked


def test_exposure_fraction_is_deployed_over_capital() -> None:
    assert exposure_fraction(200.0, 1000.0) == 0.2


@given(_money, st.floats(min_value=0.01, max_value=1e9, allow_nan=False))
def test_exposure_fraction_in_unit_when_within_capital(deployed: float, capital: float) -> None:
    fraction = exposure_fraction(min(deployed, capital), capital)
    assert 0.0 <= fraction <= 1.0


def test_exposure_fraction_is_fully_exposed_without_capital() -> None:
    assert exposure_fraction(100.0, 0.0) == 1.0
    assert exposure_fraction(100.0, -5.0) == 1.0


def _position(size: float, category: str, opened_at: datetime) -> OpenPosition:
    return OpenPosition(
        size=size, category=category, correlation_group=category, opened_at=opened_at
    )


def test_exposure_state_aggregates_by_category_and_group() -> None:
    positions = [
        _position(100.0, "politics", _NOW),
        _position(50.0, "politics", _NOW),
        _position(30.0, "sports", _NOW),
    ]
    state = exposure_state(positions, asof=_NOW, daily_window=_DAY, weekly_window=_WEEK)
    assert state.per_category == {"politics": 150.0, "sports": 30.0}
    assert state.per_group == {"politics": 150.0, "sports": 30.0}
    assert state.absolute_total == 180.0
    assert state.daily_total == 180.0


def test_exposure_state_windows_exclude_older_positions() -> None:
    positions = [
        _position(100.0, "x", _NOW),
        _position(50.0, "x", _NOW - timedelta(days=3)),
        _position(20.0, "x", _NOW - timedelta(days=30)),
    ]
    state = exposure_state(positions, asof=_NOW, daily_window=_DAY, weekly_window=_WEEK)
    assert state.absolute_total == 170.0
    assert state.weekly_total == 150.0
    assert state.daily_total == 100.0


@given(st.lists(st.tuples(_money, st.sampled_from(["a", "b", "c"])), max_size=20))
def test_exposure_absolute_equals_sum_and_categories_partition(
    items: list[tuple[float, str]],
) -> None:
    positions = [_position(size, category, _NOW) for size, category in items]
    state = exposure_state(positions, asof=_NOW, daily_window=_DAY, weekly_window=_WEEK)
    assert state.absolute_total == pytest.approx(sum(size for size, _ in items))
    assert sum(state.per_category.values()) == pytest.approx(state.absolute_total)
