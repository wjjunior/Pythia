"""Hypothesis property tests for the calibration metrics (§4.6 invariant 3)."""

from hypothesis import given
from hypothesis import strategies as st

from pythia.eval import brier, brier_score, reliability_bins

unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
outcome = st.integers(min_value=0, max_value=1)
pairs = st.lists(st.tuples(unit, outcome), min_size=1, max_size=50)


@given(unit, outcome)
def test_brier_stays_in_the_unit_interval(forecast: float, observed: int) -> None:
    assert 0.0 <= brier(forecast, observed) <= 1.0


@given(outcome)
def test_brier_of_an_outcome_against_itself_is_zero(observed: int) -> None:
    assert brier(float(observed), observed) == 0.0


@given(pairs)
def test_brier_score_stays_in_the_unit_interval(rows: list[tuple[float, int]]) -> None:
    forecasts = [f for f, _ in rows]
    outcomes = [o for _, o in rows]
    assert 0.0 <= brier_score(forecasts, outcomes) <= 1.0


@given(pairs, st.integers(min_value=1, max_value=20))
def test_reliability_bins_partition_and_stay_in_range(
    rows: list[tuple[float, int]], n_bins: int
) -> None:
    forecasts = [f for f, _ in rows]
    outcomes = [o for _, o in rows]
    bins = reliability_bins(forecasts, outcomes, n_bins=n_bins)
    assert sum(b.count for b in bins) == len(forecasts)
    for b in bins:
        assert b.count >= 1
        assert 0.0 <= b.observed_frequency <= 1.0
        assert b.lower - 1e-9 <= b.mean_forecast <= b.upper + 1e-9
