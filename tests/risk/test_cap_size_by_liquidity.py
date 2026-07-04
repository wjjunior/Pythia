"""Tests for cap_size_by_liquidity."""

from hypothesis import given
from hypothesis import strategies as st

from pythia.risk import RiskReason
from pythia.risk.functions import cap_size_by_liquidity


def test_cap_cuts_and_flags_when_size_exceeds_liquidity() -> None:
    assert cap_size_by_liquidity(100.0, 40.0) == (40.0, RiskReason.SIZE_EXCEEDS_LIQUIDITY)


def test_cap_is_a_noop_within_liquidity() -> None:
    assert cap_size_by_liquidity(30.0, 40.0) == (30.0, None)


@given(size=st.floats(0.0, 1e6), liq=st.floats(0.0, 1e6))
def test_capped_never_exceeds_liquidity(size: float, liq: float) -> None:
    capped, reason = cap_size_by_liquidity(size, liq)
    assert capped <= liq + 1e-9
    assert (reason is RiskReason.SIZE_EXCEEDS_LIQUIDITY) == (size > liq)
