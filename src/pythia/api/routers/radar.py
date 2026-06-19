"""Opportunity radar (§A.1): opportunities ranked by opportunity_score."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep
from pythia.api.schemas import RadarItem
from pythia.db.repositories import ScoreRepository

router = APIRouter(tags=["radar"])


@router.get("/api/radar")
def radar(session: SessionDep, limit: int = 20) -> list[RadarItem]:
    rows = ScoreRepository(session).radar(limit)
    return [
        RadarItem(market=market, estimate=estimate, score=score) for score, estimate, market in rows
    ]
