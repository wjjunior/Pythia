"""Paper ledger against real Postgres: open at the observed price, resolve to PnL (§6 Phase 5)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, PaperTrade, Side, TradeStatus
from pythia.db import models
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.paper import PaperLedger, fill_price, realize_pnl

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt21-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt21-snapshot"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt21-estimate"))
OPENED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
YES_PRICE = 0.5
SPREAD = 0.04


def _seed(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt21",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="rule",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=OPENED_AT,
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
            p_model=0.7,
            confidence=0.8,
            lower=0.6,
            upper=0.8,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )


def _trade(side: Side, size: float) -> PaperTrade:
    return PaperTrade(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt21-trade-{side}")),
        market_id=MARKET_ID,
        estimate_id=ESTIMATE_ID,
        side=side,
        entry_price=YES_PRICE,
        size=size,
        status=TradeStatus.OPEN,
        opened_at=OPENED_AT,
    )


def test_open_records_a_cost_modeled_fill_at_the_observed_price(session: Session) -> None:
    _seed(session)
    opened = PaperLedger(session).open(_trade(Side.YES, 100.0))
    assert opened.status == TradeStatus.OPEN
    assert opened.entry_price == pytest.approx(
        fill_price(YES_PRICE, Side.YES, SPREAD, fee=0.0, slippage=0.0)
    )
    assert opened.pnl is None
    assert opened.resolved_at is None


def test_resolve_settles_open_trades_with_pnl(session: Session) -> None:
    _seed(session)
    ledger = PaperLedger(session)
    opened = ledger.open(_trade(Side.YES, 100.0))
    resolved_at = OPENED_AT + timedelta(days=10)

    ledger.resolve(MARKET_ID, outcome=1, at=resolved_at)

    row = session.get(models.PaperTrade, uuid.UUID(opened.id))
    assert row is not None
    assert row.status == TradeStatus.RESOLVED
    assert row.resolved_at == resolved_at
    assert row.pnl == pytest.approx(realize_pnl(Side.YES, opened.entry_price, 100.0, 1))
    assert row.pnl is not None and row.pnl > 0.0


def test_resolve_marks_a_losing_trade_at_minus_stake(session: Session) -> None:
    _seed(session)
    ledger = PaperLedger(session)
    opened = ledger.open(_trade(Side.NO, 100.0))
    ledger.resolve(MARKET_ID, outcome=1, at=OPENED_AT + timedelta(days=1))
    row = session.get(models.PaperTrade, uuid.UUID(opened.id))
    assert row is not None
    assert row.pnl == pytest.approx(-100.0)


def test_resolve_is_a_noop_for_already_resolved_trades(session: Session) -> None:
    _seed(session)
    ledger = PaperLedger(session)
    opened = ledger.open(_trade(Side.YES, 100.0))
    ledger.resolve(MARKET_ID, outcome=1, at=OPENED_AT + timedelta(days=1))
    first = session.get(models.PaperTrade, uuid.UUID(opened.id))
    assert first is not None
    first_pnl = first.pnl

    ledger.resolve(MARKET_ID, outcome=0, at=OPENED_AT + timedelta(days=2))
    second = session.get(models.PaperTrade, uuid.UUID(opened.id))
    assert second is not None
    assert second.pnl == first_pnl
