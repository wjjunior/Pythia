"""Pure recalibration (§4.9): point-in-time fit/apply, monotonicity, leakage, comparison."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from pythia.eval.recalibration import (
    ISOTONIC_MIN_N,
    CalibrationMap,
    compare_brier,
    fit,
    recalibrate,
    select_method,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_BEFORE = _T0 - timedelta(days=1)

# p=0.6 resolves YES 80% of the time, p=0.4 resolves YES 20% — a monotone miscalibration.
_MISCAL = ([(0.6, 1)] * 4 + [(0.6, 0)] + [(0.4, 0)] * 4 + [(0.4, 1)]) * 20
_PLATT = fit("platt", [p for p, _ in _MISCAL], [o for _, o in _MISCAL])


def test_identity_map_returns_p_unchanged() -> None:
    assert CalibrationMap(method="identity", params={}).apply(0.42) == 0.42


def test_select_method_by_sample_size() -> None:
    assert select_method(10, "platt", 50) == "identity"
    assert select_method(100, "platt", 50) == "platt"
    assert select_method(100, "beta", 50) == "beta"
    assert select_method(ISOTONIC_MIN_N, "platt", 50) == "isotonic"


def test_recalibrate_below_min_n_is_identity() -> None:
    samples = [(0.6, 1, _BEFORE), (0.4, 0, _BEFORE)]
    cmap = recalibrate(samples, asof=_T0, method="platt", min_n=50)
    assert cmap.method == "identity"
    assert cmap.apply(0.6) == 0.6


def test_point_in_time_excludes_resolutions_at_or_after_asof() -> None:
    before = [(p, o, _BEFORE) for p, o in _MISCAL]
    leaked = [*before, (0.99, 1, _T0), (0.01, 0, _T0 + timedelta(days=1))]
    fit_before = recalibrate(before, asof=_T0, method="platt", min_n=10)
    fit_leaked = recalibrate(leaked, asof=_T0, method="platt", min_n=10)
    assert fit_leaked == fit_before


@given(st.floats(min_value=0.0, max_value=1.0), st.floats(min_value=0.0, max_value=1.0))
def test_platt_map_is_monotone(a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    assert _PLATT.apply(lo) <= _PLATT.apply(hi) + 1e-9


def test_platt_improves_brier_on_miscalibrated_data() -> None:
    report = compare_brier(_MISCAL, _PLATT)
    assert report.brier_recalibrated < report.brier_raw
    assert report.n == len(_MISCAL)


def test_isotonic_is_chosen_for_large_n_and_stays_monotone() -> None:
    rows = ([(0.6, 1)] * 4 + [(0.6, 0)] + [(0.4, 0)] * 4 + [(0.4, 1)]) * 250
    samples = [(p, o, _BEFORE) for p, o in rows]
    cmap = recalibrate(samples, asof=_T0, method="platt", min_n=50)
    assert cmap.method == "isotonic"
    assert cmap.apply(0.4) <= cmap.apply(0.6)


@given(st.floats(min_value=0.0, max_value=1.0))
def test_apply_stays_in_unit_interval(p: float) -> None:
    assert 0.0 <= _PLATT.apply(p) <= 1.0
