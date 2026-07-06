from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from pythia.scoring.fees import (
    DEFAULT_FEE_SCHEDULE,
    fee_family,
    rate_asof,
    taker_fee,
    taker_fee_fn,
    taker_fee_for,
)

_2026 = datetime(2026, 6, 1, tzinfo=UTC)  # after the 2026-01-01 effective_from
_2025 = datetime(2025, 6, 1, tzinfo=UTC)  # before any version


def test_taker_fee_zero_when_rate_zero() -> None:
    assert taker_fee(0.0, 0.5) == 0.0


@given(rate=st.floats(0.0, 1.0, allow_nan=False), p=st.floats(0.0, 1.0, allow_nan=False))
def test_taker_fee_non_negative(rate: float, p: float) -> None:
    assert taker_fee(rate, p) >= 0.0


@given(rate=st.floats(0.0, 1.0, allow_nan=False), p=st.floats(0.0, 1.0, allow_nan=False))
def test_taker_fee_max_at_half(rate: float, p: float) -> None:
    assert taker_fee(rate, 0.5) >= taker_fee(rate, p) - 1e-12


def test_fee_family_normalizes_and_aliases() -> None:
    assert fee_family("Crypto") == "crypto"
    assert fee_family("world affairs") == "geopolitics"
    assert fee_family("Crypto Prices") == "crypto"
    assert fee_family("uncategorized") == "uncategorized"  # unmapped stays lowercased self


def test_rate_asof_uses_version_and_default() -> None:
    assert rate_asof(DEFAULT_FEE_SCHEDULE, "crypto", _2026, 0.04) == 0.07
    assert rate_asof(DEFAULT_FEE_SCHEDULE, "geopolitics", _2026, 0.04) == 0.0
    assert rate_asof(DEFAULT_FEE_SCHEDULE, "space", _2026, 0.04) == 0.04  # unmapped -> default
    assert rate_asof(DEFAULT_FEE_SCHEDULE, "crypto", _2025, 0.04) == 0.0  # pre-fee era


def test_taker_fee_for_and_fn() -> None:
    assert taker_fee_for(DEFAULT_FEE_SCHEDULE, "sports", 0.5, _2026, 0.04) == 0.03 * 0.25
    fn = taker_fee_fn(DEFAULT_FEE_SCHEDULE, 0.04, flat_fee=0.01)
    assert fn("sports", 0.5, _2026) == 0.01 + 0.03 * 0.25
