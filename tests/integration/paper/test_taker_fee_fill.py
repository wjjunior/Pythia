"""PYT-190 T4: PaperLedger.fee_fn — per-category taker fee applied at fill time (§1.2, §3.2).

Asserts that when fee_fn is provided, the fill price uses fee_fn(category, p, opened_at) as the
sole fee source (no double-count with self._fee) and that a pre-schedule fill is fee-free.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import (
    Estimate,
    Market,
    MarketSnapshot,
    PaperTrade,
    Side,
    SignalSource,
    TradeStatus,
)
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.paper import PaperLedger, fill_price
from pythia.scoring.fees import DEFAULT_FEE_SCHEDULE, taker_fee, taker_fee_fn

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt190-t4-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt190-t4-snapshot"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt190-t4-estimate"))
TRADE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt190-t4-trade"))

# 2026-01-01 is the DEFAULT_FEE_SCHEDULE effective date; any 2026 timestamp is post-schedule.
OPENED_AT = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
SNAPSHOT_TS = datetime(2026, 6, 15, 9, 0, tzinfo=UTC)  # <= OPENED_AT so it is the fill source

YES_PRICE = 0.5
SPREAD = 0.02
# crypto rate from DEFAULT_FEE_SCHEDULE is 0.07; no flat_fee passed (default 0.0)
EXPECTED_FEE = taker_fee(0.07, YES_PRICE)  # 0.07 * 0.5 * 0.5 = 0.0175


def _seed(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt190-t4",
            question="Will PYT-190 T4 succeed?",
            category="crypto",
            resolution_rule="manual",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=SNAPSHOT_TS,
            yes_price=YES_PRICE,
            spread=SPREAD,
            volume=1000.0,
            liquidity=5000.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=ESTIMATE_ID,
            market_id=MARKET_ID,
            snapshot_id=SNAPSHOT_ID,
            p_model=0.6,
            confidence=0.8,
            lower=0.5,
            upper=0.7,
            rationale="stub",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )


def test_fee_fn_applies_per_category_taker_fee(session: Session) -> None:
    """fee_fn uses the market's category and opened_at to look up the taker rate.

    crypto rate = 0.07; taker_fee(0.07, 0.5) = 0.07 * 0.5 * 0.5 = 0.0175.
    No flat_fee is passed so there is no double-count.
    """
    _seed(session)

    fee_fn = taker_fee_fn(DEFAULT_FEE_SCHEDULE, 0.04)  # 0.04 default; crypto overrides to 0.07
    ledger = PaperLedger(session, fee_fn=fee_fn, slippage=0.0)

    trade = PaperTrade(
        id=TRADE_ID,
        market_id=MARKET_ID,
        estimate_id=ESTIMATE_ID,
        side=Side.YES,
        entry_price=0.0,  # ledger overwrites this
        size=100.0,
        status=TradeStatus.OPEN,
        opened_at=OPENED_AT,
        signal_source=SignalSource.UNKNOWN,
    )
    opened = ledger.open(trade)

    expected = fill_price(YES_PRICE, Side.YES, SPREAD, fee=EXPECTED_FEE, slippage=0.0)
    assert opened.entry_price == pytest.approx(expected), (
        f"entry_price {opened.entry_price!r} != fill_price with taker fee "
        f"fee={EXPECTED_FEE!r}: expected {expected!r}"
    )


def test_fee_fn_pre_schedule_fill_is_fee_free(session: Session) -> None:
    """A fill with opened_at before the schedule effective date pays zero taker fee."""
    _seed(session)

    pre_schedule = datetime(2025, 12, 31, 23, 59, tzinfo=UTC)

    # Add a snapshot that the pre-schedule trade can fill against
    pre_snap_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt190-t4-pre-snapshot"))
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=pre_snap_id,
            market_id=MARKET_ID,
            ts=pre_schedule,
            yes_price=YES_PRICE,
            spread=SPREAD,
            volume=800.0,
            liquidity=4000.0,
        )
    )

    fee_fn = taker_fee_fn(DEFAULT_FEE_SCHEDULE, 0.04)
    ledger = PaperLedger(session, fee_fn=fee_fn, slippage=0.0)

    pre_trade = PaperTrade(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt190-t4-pre-trade")),
        market_id=MARKET_ID,
        estimate_id=None,
        side=Side.YES,
        entry_price=0.0,
        size=50.0,
        status=TradeStatus.OPEN,
        opened_at=pre_schedule,
        signal_source=SignalSource.UNKNOWN,
    )
    opened = ledger.open(pre_trade)

    # Pre-schedule: no version is effective yet → rate_asof returns 0.0 → fee = 0.0
    expected = fill_price(YES_PRICE, Side.YES, SPREAD, fee=0.0, slippage=0.0)
    assert opened.entry_price == pytest.approx(expected), (
        f"pre-schedule fill should carry zero taker fee; "
        f"entry_price={opened.entry_price!r}, expected={expected!r}"
    )


def test_no_fee_fn_falls_back_to_flat_fee(session: Session) -> None:
    """When fee_fn is None, the flat self._fee path is used (the two never combine)."""
    _seed(session)

    flat_fee = 0.03
    ledger = PaperLedger(session, fee=flat_fee, slippage=0.0)

    trade = PaperTrade(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt190-t4-flat-trade")),
        market_id=MARKET_ID,
        estimate_id=ESTIMATE_ID,
        side=Side.YES,
        entry_price=0.0,
        size=100.0,
        status=TradeStatus.OPEN,
        opened_at=OPENED_AT,
        signal_source=SignalSource.UNKNOWN,
    )
    opened = ledger.open(trade)

    expected = fill_price(YES_PRICE, Side.YES, SPREAD, fee=flat_fee, slippage=0.0)
    assert opened.entry_price == pytest.approx(expected), (
        f"flat-fee ledger: entry_price {opened.entry_price!r} != expected {expected!r}"
    )
