"""Unit + property tests for deriving open positions from trades (PYT-132)."""

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from pythia.ingestion.falcon.models import FalconTrade
from pythia.ingestion.falcon.positions import derive_open_positions

ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _trade(
    *,
    condition_id: str = "c1",
    outcome: str = "Up",
    side: str = "BUY",
    size: float,
    price: float = 0.5,
    ts: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> FalconTrade:
    return FalconTrade(
        trade_id="t",
        wallet="0xw",
        condition_id=condition_id,
        outcome=outcome,
        side=side,
        price=price,
        size=size,
        slug="s",
        transaction_hash="0xh",
        ts=ts,
    )


def test_empty_trades_have_no_open_positions() -> None:
    assert derive_open_positions([], ASOF) == []


def test_a_single_buy_is_an_open_position_at_its_price() -> None:
    [pos] = derive_open_positions([_trade(size=100.0, price=0.4)], ASOF)
    assert (pos.condition_id, pos.outcome, pos.size, pos.avg_price) == ("c1", "Up", 100.0, 0.4)


def test_a_partial_sell_reduces_net_size_but_not_the_entry_price() -> None:
    trades = [_trade(side="BUY", size=100.0, price=0.4), _trade(side="SELL", size=30.0, price=0.9)]
    [pos] = derive_open_positions(trades, ASOF)
    assert pos.size == 70.0  # 100 bought - 30 sold
    assert pos.avg_price == 0.4  # avg entry is buy-weighted; the exit never moves it


def test_a_full_sell_closes_the_position() -> None:
    trades = [_trade(side="BUY", size=100.0), _trade(side="SELL", size=100.0)]
    assert derive_open_positions(trades, ASOF) == []


def test_selling_more_than_bought_is_not_an_open_position() -> None:
    # Conservative: net <= 0 never yields a (fabricated) position, even with partial trade history.
    trades = [_trade(side="BUY", size=40.0), _trade(side="SELL", size=100.0)]
    assert derive_open_positions(trades, ASOF) == []


def test_multiple_buys_sum_size_and_size_weight_the_entry_price() -> None:
    trades = [_trade(size=90.0, price=0.9), _trade(size=10.0, price=0.5)]
    [pos] = derive_open_positions(trades, ASOF)
    assert pos.size == 100.0
    assert pos.avg_price == (90.0 * 0.9 + 10.0 * 0.5) / 100.0  # 0.86


def test_two_outcomes_of_one_market_are_separate_positions_sorted_deterministically() -> None:
    trades = [
        _trade(condition_id="c1", outcome="Up", size=69.0),
        _trade(condition_id="c1", outcome="Down", size=225.0),
    ]
    positions = derive_open_positions(trades, ASOF)
    assert [(p.outcome, p.size) for p in positions] == [("Down", 225.0), ("Up", 69.0)]


def test_a_trade_after_asof_is_excluded() -> None:
    trades = [_trade(size=100.0, ts=ASOF + timedelta(days=1))]
    assert derive_open_positions(trades, ASOF) == []


_TRADE_ST = st.builds(
    _trade,
    condition_id=st.sampled_from(["c1", "c2"]),
    outcome=st.sampled_from(["Up", "Down"]),
    side=st.sampled_from(["BUY", "SELL"]),
    size=st.floats(min_value=1.0, max_value=1000.0),
    price=st.floats(min_value=0.01, max_value=0.99),
    ts=st.datetimes(
        min_value=datetime(2025, 1, 1),
        max_value=datetime(2027, 1, 1),
        timezones=st.just(UTC),
    ),
)


@given(trades=st.lists(_TRADE_ST, max_size=30), asof=st.datetimes(timezones=st.just(UTC)))
def test_trades_after_asof_never_affect_the_result(
    trades: list[FalconTrade], asof: datetime
) -> None:
    # AC-S3: a position is reconstructed only from trades at or before asof — no lookahead.
    kept = [t for t in trades if t.ts <= asof]
    assert derive_open_positions(trades, asof) == derive_open_positions(kept, asof)
