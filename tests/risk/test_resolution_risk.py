"""Pure resolution/dispute risk (§4.4.2): invalid-rate, P_resol grading, paper gate."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.risk.resolution_risk import grade_p_resol, invalid_rate, is_resolution_gated


def test_invalid_rate_is_share_of_ambiguous_resolutions() -> None:
    assert invalid_rate(2, 10) == pytest.approx(0.2)
    assert invalid_rate(0, 0) == 0.0


def test_grade_scales_penalty_by_one_minus_invalid_rate() -> None:
    assert grade_p_resol(0.8, 0.0) == pytest.approx(0.8)
    assert grade_p_resol(0.8, 0.25) == pytest.approx(0.6)
    assert grade_p_resol(0.8, 1.0) == pytest.approx(0.0)


@given(
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=0.0, max_value=1.0),
)
def test_grade_is_monotone_decreasing_in_invalid_rate(base: float, r1: float, r2: float) -> None:
    lo, hi = sorted((r1, r2))
    assert grade_p_resol(base, hi) <= grade_p_resol(base, lo) + 1e-9


@given(st.floats(min_value=0.0, max_value=1.0), st.floats(min_value=0.0, max_value=1.0))
def test_graded_penalty_stays_in_unit_interval(base: float, rate: float) -> None:
    assert 0.0 <= grade_p_resol(base, rate) <= 1.0


def test_resolution_gate_trips_strictly_above_the_threshold() -> None:
    assert is_resolution_gated(0.2, 0.15) is True
    assert is_resolution_gated(0.1, 0.15) is False
    assert is_resolution_gated(0.15, 0.15) is False
