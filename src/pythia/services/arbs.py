"""Arb-basket approval (PYT-145): open a detected basket's legs as market-anchored paper trades.

Arb legs are price plays, not model forecasts — each fills from its market's latest snapshot, with
no estimate. Every leg buys the same ``baskets`` shares (dollar size = baskets * leg.price), so the
basket's realized PnL locks the detected edge: realized ≈ baskets * arb.ev (net of fees). The legs
open via ``open_basket`` (all-or-none) and so are exempt from the single-market double-YES veto.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from pythia.common import ArbLeg, ArbOpportunity, PaperTrade, Side, SignalSource, TradeStatus
from pythia.config import Settings, ensure_paper_mode
from pythia.db import models
from pythia.db.repositories import ArbRepository
from pythia.paper import PaperLedger

_PLACEHOLDER_ENTRY = 0.0  # the ledger refills from the snapshot; this value is never persisted


class UnknownArbError(LookupError):
    """No persisted arb with this id — nothing to approve."""


class ArbAlreadyApprovedError(RuntimeError):
    """This arb's basket is already open — its deterministic leg ids would collide."""


def drop_stale_arbs(
    arbs: Sequence[ArbOpportunity], stale_market_ids: set[str]
) -> list[ArbOpportunity]:
    """Arbs with no stale leg — none references a resolved/closed market (PYT-175).

    An arb is only tradeable if you can enter all legs; a single resolved/closed leg makes the whole
    basket stale, so it is dropped from the panel (query-time, non-destructive).
    """
    return [arb for arb in arbs if not any(leg.market_id in stale_market_ids for leg in arb.legs)]


def arb_leg_trade_id(arb_id: str, market_id: str, side: Side) -> str:
    """The deterministic paper-trade id for an arb leg — the approve path and the approved-state
    check share it so they cannot diverge."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"arb|{arb_id}|{market_id}|{side.value}"))


def approved_arb_ids(session: Session, arbs: Sequence[ArbOpportunity]) -> set[str]:
    """Ids of arbs whose basket is already open — any leg's deterministic trade id exists in the
    ledger (open_basket is all-or-none, so one present leg means the whole basket was opened)."""
    by_trade_id: dict[uuid.UUID, str] = {}
    for arb in arbs:
        if arb.id is None:
            continue
        for leg in arb.legs:
            by_trade_id[uuid.UUID(arb_leg_trade_id(arb.id, leg.market_id, leg.side))] = arb.id
    if not by_trade_id:
        return set()
    existing = session.exec(
        select(models.PaperTrade.id).where(col(models.PaperTrade.id).in_(list(by_trade_id)))
    ).all()
    return {by_trade_id[row] for row in existing if row is not None}


def _leg_trade(arb_id: str, leg: ArbLeg, baskets: float, at: datetime) -> PaperTrade:
    return PaperTrade(
        id=arb_leg_trade_id(arb_id, leg.market_id, leg.side),
        market_id=leg.market_id,
        estimate_id=None,
        side=leg.side,
        entry_price=_PLACEHOLDER_ENTRY,
        size=baskets * leg.price,
        status=TradeStatus.OPEN,
        opened_at=at,
        signal_source=SignalSource.ARB,
    )


class ArbApprovalService:
    def __init__(self, session: Session, ledger: PaperLedger, settings: Settings) -> None:
        self._arbs = ArbRepository(session)
        self._ledger = ledger
        self._settings = settings

    def approve(self, arb_id: str, *, baskets: float, at: datetime) -> list[PaperTrade]:
        ensure_paper_mode(self._settings)
        if baskets <= 0.0:
            raise ValueError("baskets must be positive")
        arb = self._arbs.get(arb_id)
        if arb is None:
            raise UnknownArbError(arb_id)
        trades = [_leg_trade(arb_id, leg, baskets, at) for leg in arb.legs]
        try:
            return self._ledger.open_basket(trades)
        except IntegrityError as exc:  # deterministic leg ids ⇒ a re-approval collides
            raise ArbAlreadyApprovedError(arb_id) from exc
