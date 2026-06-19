"""Opportunity radar (§A.1): opportunities ranked by opportunity_score."""

from typing import Annotated

from fastapi import APIRouter, Query

from pythia.api.deps import SessionDep
from pythia.api.schemas import RadarItem
from pythia.db.repositories import ScoreRepository

router = APIRouter(tags=["radar"])

_MAX_RADAR_LIMIT = 200


@router.get("/api/radar")
def radar(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=_MAX_RADAR_LIMIT)] = 20
) -> list[RadarItem]:
    rows = ScoreRepository(session).radar(limit)
    return [
        RadarItem(market=market, estimate=estimate, score=score) for score, estimate, market in rows
    ]
