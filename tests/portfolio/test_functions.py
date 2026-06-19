"""Property tests for the pure capital-state math (§4.4.1)."""

from hypothesis import given
from hypothesis import strategies as st

from pythia.portfolio import available_capital, exposure_fraction

_money = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


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
