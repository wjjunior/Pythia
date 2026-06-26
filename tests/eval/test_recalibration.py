"""Pure recalibration (§4.9): point-in-time fit/apply, monotonicity, leakage, comparison."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from pythia.eval.recalibration import (
    ISOTONIC_MIN_N,
    CalibrationMap,
    compare_brier,
    fit,
    map_is_point_in_time,
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


def test_fit_on_degenerate_data_yields_a_finite_usable_map() -> None:
    # single distinct p / all-same outcome must never produce a NaN/inf map.
    for cmap in (fit("platt", [0.5] * 4, [1, 1, 1, 1]), fit("beta", [0.5] * 4, [0, 0, 0, 0])):
        for p in (0.0, 0.3, 0.7, 1.0):
            assert 0.0 <= cmap.apply(p) <= 1.0


@given(
    rows=st.lists(st.tuples(st.floats(0.02, 0.98), st.integers(0, 1)), min_size=2, max_size=30),
    leak_p=st.floats(0.02, 0.98),
    leak_outcome=st.integers(0, 1),
    after_days=st.integers(0, 30),
)
def test_should_fit_only_on_resolutions_strictly_before_asof(
    rows: list[tuple[float, int]], leak_p: float, leak_outcome: int, after_days: int
) -> None:
    before = [(p, o, _BEFORE) for p, o in rows]
    # The leak samples land exactly at asof and after it; a strictly-before fit must ignore both, so
    # adding them cannot change the map. An off-by-one (< → <=) lets the at-asof sample in.
    leak = [
        (leak_p, leak_outcome, _T0),
        (leak_p, 1 - leak_outcome, _T0 + timedelta(days=after_days)),
    ]
    clean = recalibrate(before, asof=_T0, method="platt", min_n=2)
    leaked = recalibrate([*before, *leak], asof=_T0, method="platt", min_n=2)
    assert leaked == clean


@given(rows=st.lists(st.tuples(st.floats(0.02, 0.98), st.integers(0, 1)), min_size=1, max_size=30))
def test_should_fit_a_monotone_beta_map(rows: list[tuple[float, int]]) -> None:
    cmap = fit("beta", [p for p, _ in rows], [o for _, o in rows])
    grid = [i / 50 for i in range(1, 50)]
    applied = [cmap.apply(p) for p in grid]
    assert all(applied[i] <= applied[i + 1] + 1e-9 for i in range(len(applied) - 1))


def test_should_fall_back_from_isotonic_to_platt_below_isotonic_min_n() -> None:
    assert select_method(49, "isotonic", 50) == "identity"
    assert select_method(50, "isotonic", 50) == "platt"
    assert select_method(ISOTONIC_MIN_N - 1, "isotonic", 50) == "platt"
    assert select_method(ISOTONIC_MIN_N, "isotonic", 50) == "isotonic"


@given(fit_offset_days=st.integers(min_value=-30, max_value=30))
def test_should_apply_a_map_only_when_fit_asof_is_at_or_before_the_snapshot(
    fit_offset_days: int,
) -> None:
    fit_asof = _T0 + timedelta(days=fit_offset_days)
    # A map fit after the snapshot embeds resolutions the forecast could not have seen → lookahead.
    assert map_is_point_in_time(fit_asof, _T0) == (fit_asof <= _T0)


def test_should_apply_a_map_fit_exactly_at_the_snapshot() -> None:
    assert map_is_point_in_time(_T0, _T0) is True  # boundary is <=, not < (a slip fails here)


def test_should_not_apply_a_map_without_a_fit_asof() -> None:
    assert map_is_point_in_time(None, _T0) is False
