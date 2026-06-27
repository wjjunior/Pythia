"""Structural arbitrage panel (PYT-141, PYT-145): list event baskets by EV; approve to paper."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from pythia.api.deps import BroadcasterDep, SessionDep, SettingsDep
from pythia.api.events import SseChannel
from pythia.api.schemas import ArbApproveRequest, ArbApproveResult, ArbItem, ArbLegItem
from pythia.config import ExecutionModeError
from pythia.db.repositories import ArbRepository
from pythia.paper import PaperLedger, PaperLedgerError
from pythia.services.arbs import ArbAlreadyApprovedError, ArbApprovalService, UnknownArbError

router = APIRouter(tags=["arbs"])


@router.get("/api/arbs")
def arbs(session: SessionDep) -> list[ArbItem]:
    return [
        ArbItem(
            id=arb.id,
            event_slug=arb.event_slug,
            kind=arb.kind.value,
            ev=arb.ev,
            legs=[
                ArbLegItem(market_id=leg.market_id, side=leg.side.value, price=leg.price)
                for leg in arb.legs
            ],
        )
        for arb in ArbRepository(session).list_ranked()
        if arb.id is not None  # persisted rows always carry an id; never fabricate one
    ]


@router.post("/api/arbs/{arb_id}/approve")
def approve_arb(
    arb_id: uuid.UUID,
    body: ArbApproveRequest,
    session: SessionDep,
    settings: SettingsDep,
    broadcaster: BroadcasterDep,
) -> ArbApproveResult:
    service = ArbApprovalService(session, PaperLedger(session), settings)
    try:
        trades = service.approve(str(arb_id), baskets=body.baskets, at=datetime.now(UTC))
    except UnknownArbError:
        raise HTTPException(status_code=404, detail="arb not found") from None
    except ArbAlreadyApprovedError:
        raise HTTPException(status_code=409, detail="arb already approved") from None
    except ExecutionModeError:
        raise HTTPException(status_code=409, detail="not in paper mode") from None
    except PaperLedgerError:
        raise HTTPException(status_code=409, detail="a basket leg has no snapshot") from None
    for trade in trades:
        broadcaster.publish(SseChannel.PAPER_CHANGED, trade.id)
    return ArbApproveResult(arb_id=str(arb_id), trades=trades)
