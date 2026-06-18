"""Hypothesis property tests proving the §4.6 invariants for the scoring core."""

from hypothesis import given
from hypothesis import strategies as st

from pythia.scoring import (
    confidence,
    kelly_fraction,
    opportunity_score,
    position_size,
)

unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
nonneg = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


# Invariant 1 — confidence as logical AND
@given(unit, unit, unit)
def test_confidence_stays_in_the_unit_interval(a: float, b: float, c: float) -> None:
    assert 0.0 <= confidence(a, b, c) <= 1.0


@given(unit, unit, unit)
def test_confidence_is_zero_if_any_factor_is_zero(a: float, b: float, c: float) -> None:
    if a == 0.0 or b == 0.0 or c == 0.0:
        assert confidence(a, b, c) == 0.0


@given(unit, unit, unit)
def test_confidence_is_monotonic_non_decreasing_in_each_factor(
    a: float, b: float, c: float
) -> None:
    base = confidence(a, b, c)
    assert confidence(min(1.0, a + 0.1), b, c) >= base - 1e-12
    assert confidence(a, min(1.0, b + 0.1), c) >= base - 1e-12
    assert confidence(a, b, min(1.0, c + 0.1)) >= base - 1e-12


# Invariant 2 — Kelly safety
@given(finite, st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False))
def test_kelly_fraction_is_never_negative(edge_net: float, q: float) -> None:
    assert kelly_fraction(edge_net, q) >= 0.0


@given(
    st.floats(min_value=-1e6, max_value=0.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=0.0, max_value=0.999_999, allow_nan=False, allow_infinity=False),
)
def test_kelly_fraction_is_zero_when_net_edge_not_positive(edge_net: float, q: float) -> None:
    assert kelly_fraction(edge_net, q) == 0.0


@given(nonneg, nonneg, unit, unit, nonneg)
def test_position_size_never_exceeds_the_cap(
    bankroll: float, kelly_frac: float, kelly_mult: float, conf: float, cap: float
) -> None:
    size = position_size(bankroll, kelly_frac, kelly_mult, conf, cap)
    assert 0.0 <= size <= cap


@given(nonneg, nonneg, nonneg, unit, unit, nonneg)
def test_position_size_is_non_decreasing_in_bankroll(
    b1: float, b2: float, kelly_frac: float, kelly_mult: float, conf: float, cap: float
) -> None:
    lo, hi = sorted((b1, b2))
    assert position_size(lo, kelly_frac, kelly_mult, conf, cap) <= (
        position_size(hi, kelly_frac, kelly_mult, conf, cap) + 1e-9
    )


# Invariant 6 — no silent bets
@given(nonneg, nonneg, unit, nonneg)
def test_position_size_is_zero_when_confidence_is_zero(
    bankroll: float, kelly_frac: float, kelly_mult: float, cap: float
) -> None:
    assert position_size(bankroll, kelly_frac, kelly_mult, 0.0, cap) == 0.0


@given(finite, unit, unit, unit, unit)
def test_opportunity_score_is_zero_when_a_penalty_is_zero(
    ev_unit: float, conf: float, p_liq: float, p_time: float, p_corr: float
) -> None:
    assert opportunity_score(ev_unit, conf, 0.0, p_liq, p_time, p_corr) == 0.0
