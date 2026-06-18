"""Example tests for the calibration metrics: Brier, reliability bins, market comparison."""

import pytest

from pythia.eval import (
    brier,
    brier_score,
    calibration_by_category,
    ev_predicted_vs_realized,
    reliability_bins,
)


def test_brier_perfect_forecast_is_zero() -> None:
    assert brier(1.0, 1) == 0.0
    assert brier(0.0, 0) == 0.0


def test_brier_worst_forecast_is_one() -> None:
    assert brier(0.0, 1) == 1.0
    assert brier(1.0, 0) == 1.0


def test_brier_half_is_a_quarter() -> None:
    assert brier(0.5, 1) == pytest.approx(0.25)


def test_brier_score_is_the_mean() -> None:
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([0.0, 1.0], [1, 0]) == 1.0
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)


def test_reliability_bins_partition_the_forecasts() -> None:
    forecasts = [0.05, 0.15, 0.15, 0.95]
    outcomes = [0, 1, 0, 1]
    bins = reliability_bins(forecasts, outcomes, n_bins=10)
    assert sum(b.count for b in bins) == 4
    bin_01 = next(b for b in bins if b.lower == pytest.approx(0.1))
    assert bin_01.count == 2
    assert bin_01.observed_frequency == pytest.approx(0.5)
    assert bin_01.mean_forecast == pytest.approx(0.15)


def test_reliability_bins_drop_empty_bins() -> None:
    bins = reliability_bins([0.05, 0.05], [0, 1], n_bins=10)
    assert len(bins) == 1
    assert bins[0].count == 2


def test_forecast_of_one_lands_in_the_last_bin() -> None:
    bins = reliability_bins([1.0], [1], n_bins=10)
    assert len(bins) == 1
    assert bins[0].upper == pytest.approx(1.0)
    assert bins[0].count == 1


def test_calibration_by_category_reports_delta_and_n() -> None:
    records = {"politics": [(0.9, 0.6, 1), (0.1, 0.4, 0)]}
    result = calibration_by_category(records)
    assert len(result) == 1
    calibration = result[0]
    assert calibration.category == "politics"
    assert calibration.n == 2
    assert calibration.brier_system < calibration.brier_market
    assert calibration.delta == pytest.approx(calibration.brier_market - calibration.brier_system)
    assert calibration.delta > 0


def test_ev_predicted_vs_realized_aggregates() -> None:
    ev = ev_predicted_vs_realized([0.1, 0.3], [0.0, 0.4])
    assert ev.n == 2
    assert ev.mean_predicted == pytest.approx(0.2)
    assert ev.mean_realized == pytest.approx(0.2)
