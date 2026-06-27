"""Paper ledger (§1.2, §3.2, §6 Phase 5): hypothetical fills at observed prices, PnL on resolution.

`open` fills at the observed snapshot price of the estimate's snapshot — re-read from the store, so
the ledger guarantees a trade is only ever recorded at a real observed price with cost modeling
applied. `resolve` settles every open trade on a market and writes PnL. Paper is a gate, not a
stage: there is no real-order path here.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlmodel import Session, select

from pythia.common import PaperTrade, Side, TradeStatus
from pythia.db import models
from pythia.paper.pricing import fill_price, realize_pnl

DEFAULT_FEE = 0.0
DEFAULT_SLIPPAGE = 0.0


class PaperLedgerError(RuntimeError):
    """The estimate or its snapshot is missing — the trade has no observed price to fill at."""


def _from_db_trade(row: models.PaperTrade) -> PaperTrade:
    return PaperTrade(
        id=str(row.id),
        market_id=str(row.market_id),
        estimate_id=str(row.estimate_id),
        side=Side(row.side),
        entry_price=row.entry_price,
        size=row.size,
        status=TradeStatus(row.status),
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        pnl=row.pnl,
    )


class PaperLedger:
    def __init__(
        self, session: Session, *, fee: float = DEFAULT_FEE, slippage: float = DEFAULT_SLIPPAGE
    ) -> None:
        self._session = session
        self._fee = fee
        self._slippage = slippage

    def open(self, trade: PaperTrade) -> PaperTrade:
        estimate = self._session.get(models.Estimate, uuid.UUID(trade.estimate_id))
        if estimate is None:
            raise PaperLedgerError(f"estimate {trade.estimate_id} not found")
        snapshot = self._session.get(models.MarketSnapshot, estimate.snapshot_id)
        if snapshot is None:
            raise PaperLedgerError(f"snapshot {estimate.snapshot_id} not found")
        entry_price = fill_price(
            snapshot.yes_price, trade.side, snapshot.spread, fee=self._fee, slippage=self._slippage
        )
        row = models.PaperTrade(
            id=uuid.UUID(trade.id),
            market_id=uuid.UUID(trade.market_id),
            estimate_id=uuid.UUID(trade.estimate_id),
            side=trade.side,
            entry_price=entry_price,
            size=trade.size,
            status=TradeStatus.OPEN,
            opened_at=trade.opened_at,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_trade(row)

    def _row_for(self, trade: PaperTrade) -> models.PaperTrade:
        estimate = self._session.get(models.Estimate, uuid.UUID(trade.estimate_id))
        if estimate is None:
            raise PaperLedgerError(f"estimate {trade.estimate_id} not found")
        snapshot = self._session.get(models.MarketSnapshot, estimate.snapshot_id)
        if snapshot is None:
            raise PaperLedgerError(f"snapshot {estimate.snapshot_id} not found")
        entry_price = fill_price(
            snapshot.yes_price, trade.side, snapshot.spread, fee=self._fee, slippage=self._slippage
        )
        return models.PaperTrade(
            id=uuid.UUID(trade.id),
            market_id=uuid.UUID(trade.market_id),
            estimate_id=uuid.UUID(trade.estimate_id),
            side=trade.side,
            entry_price=entry_price,
            size=trade.size,
            status=TradeStatus.OPEN,
            opened_at=trade.opened_at,
        )

    def open_basket(self, trades: Sequence[PaperTrade]) -> list[PaperTrade]:
        """Open every leg atomically: all fill at their observed price, or none do (all-or-none)."""
        rows: list[models.PaperTrade] = []
        try:
            for trade in trades:
                row = self._row_for(trade)
                self._session.add(row)
                rows.append(row)
            self._session.commit()
        except Exception:
            self._session.rollback()  # a missing leg rolls back the whole basket
            raise
        for row in rows:
            self._session.refresh(row)
        return [_from_db_trade(row) for row in rows]

    def resolve(self, market_id: str, outcome: int, at: datetime) -> None:
        rows = self._session.exec(
            select(models.PaperTrade)
            .where(models.PaperTrade.market_id == uuid.UUID(market_id))
            .where(models.PaperTrade.status == TradeStatus.OPEN)
        ).all()
        for row in rows:
            row.pnl = realize_pnl(Side(row.side), row.entry_price, row.size, outcome)
            row.status = TradeStatus.RESOLVED
            row.resolved_at = at
            self._session.add(row)
        self._session.commit()
