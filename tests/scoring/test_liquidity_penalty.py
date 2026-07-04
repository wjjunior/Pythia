"""Pure unit + property tests for liquidity_penalty (PYT-189)."""

from hypothesis import given
from hypothesis import strategies as st

from pythia.scoring.functions import liquidity_penalty


def test_liquidity_penalty_full_at_or_above_ref() -> None:
    assert liquidity_penalty(10_000.0, 10_000.0) == 1.0
    assert liquidity_penalty(50_000.0, 10_000.0) == 1.0


def test_liquidity_penalty_proportional_below_ref() -> None:
    assert liquidity_penalty(2_500.0, 10_000.0) == 0.25


@given(liq=st.floats(0.0, 1e7), ref=st.floats(1.0, 1e6))
def test_liquidity_penalty_in_unit_interval(liq: float, ref: float) -> None:
    assert 0.0 <= liquidity_penalty(liq, ref) <= 1.0
