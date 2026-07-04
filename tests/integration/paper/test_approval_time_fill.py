"""PYT-189 regression: estimate-linked trades fill at the latest snapshot <= opened_at, not the
estimation-time snapshot (PYT-189 T2)."""

import uuid
from datetime import UTC, datetime, timedelta

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

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t2-market"))
OLD_SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t2-old-snapshot"))
NEW_SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t2-new-snapshot"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t2-estimate"))

T0 = datetime(2026, 4, 1, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)  # newer snapshot: t1 > t0
OPENED_AT = T1 + timedelta(seconds=1)  # trade opened after the newer snapshot

OLD_YES_PRICE = 0.40
NEW_YES_PRICE = 0.70
SPREAD = 0.04


def _seed(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt189-t2",
            question="Will the fill use the approval-time snapshot?",
            category="politics",
            resolution_rule="manual",
        )
    )
    # Old snapshot — the one pinned to the estimate at estimation time
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=OLD_SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=T0,
            yes_price=OLD_YES_PRICE,
            spread=SPREAD,
            volume=1000.0,
            liquidity=5000.0,
        )
    )
    # Newer snapshot — exists before opened_at, should be the fill price source
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=NEW_SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=T1,
            yes_price=NEW_YES_PRICE,
            spread=SPREAD,
            volume=1200.0,
            liquidity=5500.0,
        )
    )
    # Estimate pinned to the OLD snapshot (stale estimation-time price)
    EstimateRepository(session).add(
        Estimate(
            id=ESTIMATE_ID,
            market_id=MARKET_ID,
            snapshot_id=OLD_SNAPSHOT_ID,
            p_model=0.75,
            confidence=0.8,
            lower=0.65,
            upper=0.85,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )


def test_estimate_linked_trade_fills_at_approval_time_snapshot(session: Session) -> None:
    """Entry price must reflect the market price at opened_at, not the estimation-time price."""
    _seed(session)
    trade = PaperTrade(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-t2-trade")),
        market_id=MARKET_ID,
        estimate_id=ESTIMATE_ID,
        side=Side.YES,
        entry_price=0.0,  # placeholder; ledger overwrites this
        size=100.0,
        status=TradeStatus.OPEN,
        opened_at=OPENED_AT,
        signal_source=SignalSource.UNKNOWN,
    )
    opened = PaperLedger(session).open(trade)

    expected = fill_price(NEW_YES_PRICE, Side.YES, SPREAD, fee=0.0, slippage=0.0)
    wrong = fill_price(OLD_YES_PRICE, Side.YES, SPREAD, fee=0.0, slippage=0.0)

    assert opened.entry_price == pytest.approx(expected), (
        f"entry_price {opened.entry_price!r} should be the approval-time price {expected!r} "
        f"(yes=0.70), not the estimation-time price {wrong!r} (yes=0.40)"
    )
    assert opened.entry_price != pytest.approx(wrong), (
        "entry_price must not reflect the stale estimation-time snapshot (yes=0.40)"
    )
