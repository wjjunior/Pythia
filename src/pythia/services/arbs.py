"""Arb-basket approval (PYT-145): open a detected basket's legs as market-anchored paper trades.

Arb legs are price plays, not model forecasts — each fills from its market's latest snapshot, with
no estimate. Every leg buys the same ``baskets`` shares (dollar size = baskets * leg.price), so the
basket's realized PnL locks the detected edge: realized ≈ baskets * arb.ev (net of fees). The legs
open via ``open_basket`` (all-or-none) and so are exempt from the single-market double-YES veto.
"""

import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pythia.common import ArbLeg, PaperTrade, TradeStatus
from pythia.config import Settings, ensure_paper_mode
from pythia.db.repositories import ArbRepository
from pythia.paper import PaperLedger

_PLACEHOLDER_ENTRY = 0.0  # the ledger refills from the snapshot; this value is never persisted


class UnknownArbError(LookupError):
    """No persisted arb with this id — nothing to approve."""


class ArbAlreadyApprovedError(RuntimeError):
    """This arb's basket is already open — its deterministic leg ids would collide."""


def _leg_trade(arb_id: str, leg: ArbLeg, baskets: float, at: datetime) -> PaperTrade:
    return PaperTrade(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"arb|{arb_id}|{leg.market_id}|{leg.side.value}")),
        market_id=leg.market_id,
        estimate_id=None,
        side=leg.side,
        entry_price=_PLACEHOLDER_ENTRY,
        size=baskets * leg.price,
        status=TradeStatus.OPEN,
        opened_at=at,
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
