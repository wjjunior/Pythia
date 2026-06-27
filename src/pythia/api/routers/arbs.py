"""Structural arbitrage panel (PYT-141): event-level dutch-book / ladder-spread baskets, by EV."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep
from pythia.api.schemas import ArbItem, ArbLegItem
from pythia.db.repositories import ArbRepository

router = APIRouter(tags=["arbs"])


@router.get("/api/arbs")
def arbs(session: SessionDep) -> list[ArbItem]:
    return [
        ArbItem(
            event_slug=arb.event_slug,
            kind=arb.kind.value,
            ev=arb.ev,
            legs=[
                ArbLegItem(market_id=leg.market_id, side=leg.side.value, price=leg.price)
                for leg in arb.legs
            ],
        )
        for arb in ArbRepository(session).list_ranked()
    ]
