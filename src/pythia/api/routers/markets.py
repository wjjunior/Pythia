"""Markets endpoints (§A.1): list/filter and detail."""

from fastapi import APIRouter, HTTPException

from pythia.api.deps import SessionDep
from pythia.api.schemas import MarketDetail
from pythia.common import Market
from pythia.db.repositories import (
    EstimateRepository,
    EvidenceRepository,
    MarketRepository,
    SnapshotRepository,
)

router = APIRouter(tags=["markets"])


@router.get("/api/markets")
def list_markets(
    session: SessionDep, category: str | None = None, status: str | None = None
) -> list[Market]:
    return MarketRepository(session).list(category=category, status=status)


@router.get("/api/markets/{market_id}")
def market_detail(market_id: str, session: SessionDep) -> MarketDetail:
    market = MarketRepository(session).get(market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")
    latest = EstimateRepository(session).latest_for_market(market_id)
    evidence = EvidenceRepository(session).get_by_ids(latest.evidence_ids) if latest else []
    return MarketDetail(
        market=market,
        snapshots=SnapshotRepository(session).list_for_market(market_id),
        latest_estimate=latest,
        evidence=evidence,
    )
