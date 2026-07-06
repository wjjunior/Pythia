"""Paper ledger (§1.2, §3.2, §6 Phase 5): hypothetical fills at observed prices, PnL on resolution.

`open` fills at the observed snapshot price of the estimate's snapshot — re-read from the store, so
the ledger guarantees a trade is only ever recorded at a real observed price with cost modeling
applied. `resolve` settles every open trade on a market and writes PnL. Paper is a gate, not a
stage: there is no real-order path here.
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime

from sqlmodel import Session, col, select

from pythia.common import PaperTrade, Side, SignalSource, TradeStatus
from pythia.db import models
from pythia.paper.pricing import fill_price, realize_pnl


class PaperLedgerError(RuntimeError):
    """No price to fill at — the estimate, its snapshot, or the market's snapshot is missing."""


class UnknownTradeError(LookupError):
    """No paper trade matches the id."""


class NotOpenError(RuntimeError):
    """Only an OPEN position can be voided — a resolved/void one is terminal."""


def _from_db_trade(row: models.PaperTrade) -> PaperTrade:
    return PaperTrade(
        id=str(row.id),
        market_id=str(row.market_id),
        estimate_id=str(row.estimate_id) if row.estimate_id is not None else None,
        side=Side(row.side),
        entry_price=row.entry_price,
        size=row.size,
        status=TradeStatus(row.status),
        opened_at=row.opened_at,
        signal_source=(
            SignalSource(row.signal_source)
            if row.signal_source is not None
            else SignalSource.UNKNOWN
        ),
        resolved_at=row.resolved_at,
        pnl=row.pnl,
    )


class PaperLedger:
    def __init__(
        self,
        session: Session,
        *,
        fee: float = 0.0,
        slippage: float = 0.0,
        fee_fn: Callable[[str, float, datetime], float] | None = None,
    ) -> None:
        self._session = session
        self._fee = fee
        self._slippage = slippage
        self._fee_fn = fee_fn

    def open(self, trade: PaperTrade) -> PaperTrade:
        row = self._row_for(trade)
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return _from_db_trade(row)

    def _snapshot_for(self, trade: PaperTrade) -> models.MarketSnapshot:
        # Fill at the latest snapshot <= approval time (PYT-189): estimate-linked trades no longer
        # re-read the stale estimation snapshot; both kinds price off the market at opened_at.
        snapshot = self._session.exec(
            select(models.MarketSnapshot)
            .where(models.MarketSnapshot.market_id == uuid.UUID(trade.market_id))
            .where(col(models.MarketSnapshot.ts) <= trade.opened_at)
            .order_by(col(models.MarketSnapshot.ts).desc())
        ).first()
        if snapshot is None:
            raise PaperLedgerError(f"no snapshot <= {trade.opened_at} for market {trade.market_id}")
        return snapshot

    def _row_for(self, trade: PaperTrade) -> models.PaperTrade:
        snapshot = self._snapshot_for(trade)
        if self._fee_fn is not None:
            market = self._session.get(models.Market, uuid.UUID(trade.market_id))
            category = market.category if market is not None else ""
            fee = self._fee_fn(category, snapshot.yes_price, trade.opened_at)
        else:
            fee = self._fee
        entry_price = fill_price(
            snapshot.yes_price, trade.side, snapshot.spread, fee=fee, slippage=self._slippage
        )
        return models.PaperTrade(
            id=uuid.UUID(trade.id),
            market_id=uuid.UUID(trade.market_id),
            estimate_id=uuid.UUID(trade.estimate_id) if trade.estimate_id is not None else None,
            side=trade.side,
            entry_price=entry_price,
            size=trade.size,
            status=TradeStatus.OPEN,
            opened_at=trade.opened_at,
            signal_source=trade.signal_source.value,
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

    def void(self, trade_id: str, at: datetime) -> PaperTrade:
        """Manually remove an OPEN position (PYT-181): pnl=0, VOID — the locked capital is released
        and it never counts toward realized PnL. A resolved/void trade cannot be voided."""
        row = self._session.get(models.PaperTrade, uuid.UUID(trade_id))
        if row is None:
            raise UnknownTradeError(trade_id)
        if row.status != TradeStatus.OPEN:
            raise NotOpenError(trade_id)
        row.status = TradeStatus.VOID
        row.pnl = 0.0
        row.resolved_at = at
        self._session.add(row)
        self._session.commit()
        return _from_db_trade(row)
