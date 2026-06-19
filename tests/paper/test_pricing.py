"""Tests for the pure paper-ledger pricing: cost-modeled fill price and PnL."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.common import Side
from pythia.paper import fill_price, realize_pnl


def test_fill_price_crosses_the_spread_for_yes() -> None:
    assert fill_price(0.5, Side.YES, 0.04, fee=0.0, slippage=0.0) == pytest.approx(0.52)


def test_fill_price_mirrors_to_the_no_side() -> None:
    assert fill_price(0.7, Side.NO, 0.0, fee=0.0, slippage=0.0) == pytest.approx(0.3)
    assert fill_price(0.5, Side.NO, 0.04, fee=0.0, slippage=0.0) == pytest.approx(0.52)


def test_fill_price_adds_fee_and_slippage() -> None:
    assert fill_price(0.5, Side.YES, 0.0, fee=0.01, slippage=0.02) == pytest.approx(0.53)


def test_fill_price_clamps_to_one() -> None:
    assert fill_price(0.99, Side.YES, 0.1, fee=0.0, slippage=0.0) == 1.0


def test_realize_pnl_yes_win_doubles_at_even_odds() -> None:
    assert realize_pnl(Side.YES, 0.5, 100.0, 1) == pytest.approx(100.0)


def test_realize_pnl_yes_loss_is_minus_stake() -> None:
    assert realize_pnl(Side.YES, 0.5, 100.0, 0) == -100.0


def test_realize_pnl_no_win() -> None:
    assert realize_pnl(Side.NO, 0.4, 100.0, 0) == pytest.approx(150.0)


def test_realize_pnl_no_loss_is_minus_stake() -> None:
    assert realize_pnl(Side.NO, 0.4, 100.0, 1) == -100.0


@given(
    st.sampled_from([Side.YES, Side.NO]),
    st.floats(min_value=0.01, max_value=1.0),
    st.floats(min_value=1.0, max_value=1e6),
    st.sampled_from([0, 1]),
)
def test_pnl_is_minus_stake_on_loss_and_nonnegative_on_win(
    side: Side, entry_price: float, size: float, outcome: int
) -> None:
    pnl = realize_pnl(side, entry_price, size, outcome)
    won = (side == Side.YES and outcome == 1) or (side == Side.NO and outcome == 0)
    if won:
        assert pnl >= 0.0
    else:
        assert pnl == pytest.approx(-size)
