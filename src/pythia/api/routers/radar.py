"""Opportunity radar (§A.1): ranked by opportunity_score, or a near-term calibration queue."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from pythia.api.deps import SessionDep
from pythia.api.schemas import RadarItem
from pythia.db.repositories import ScoreRepository

router = APIRouter(tags=["radar"])

_MAX_RADAR_LIMIT = 200


@router.get("/api/radar")
def radar(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_RADAR_LIMIT)] = 20,
    max_days_to_resolve: Annotated[int | None, Query(ge=1)] = None,
    min_win_multiple: Annotated[float | None, Query(ge=1.0)] = None,
    min_win_probability: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> list[RadarItem]:
    rows = ScoreRepository(session).radar(
        limit,
        asof=datetime.now(UTC),
        max_days_to_resolve=max_days_to_resolve,
        min_win_multiple=min_win_multiple,
        min_win_probability=min_win_probability,
    )
    return [
        RadarItem(
            market=market,
            estimate=estimate,
            score=score,
            recommended_side=metrics.recommended_side,
            entry_price=metrics.entry_price,
            win_multiple=metrics.win_multiple,
            win_probability=metrics.win_probability,
            closes_in_days=metrics.closes_in_days,
            resolves_in_days=metrics.resolves_in_days,
        )
        for score, estimate, market, metrics in rows
    ]
