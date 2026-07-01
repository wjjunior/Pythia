"""Opportunity radar (§A.1): ranked by opportunity_score, or a near-term calibration queue."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import RadarItem
from pythia.db.repositories import ScoreRepository
from pythia.scoring import rank_radar
from pythia.services.review_status import review_status_by_market
from pythia.services.risk_decision import suggested_size_by_estimate

router = APIRouter(tags=["radar"])

_MAX_RADAR_LIMIT = 200


@router.get("/api/radar")
def radar(
    session: SessionDep,
    settings: SettingsDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_RADAR_LIMIT)] = 20,
    max_days_to_resolve: Annotated[int | None, Query(ge=1)] = None,
    min_win_multiple: Annotated[float | None, Query(ge=1.0)] = None,
    min_win_probability: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
) -> list[RadarItem]:
    asof = datetime.now(UTC)
    candidates = ScoreRepository(session).radar(
        asof=asof,
        max_days_to_resolve=max_days_to_resolve,
        min_liquidity=settings.radar_min_liquidity,
    )
    rows = rank_radar(
        candidates,
        asof=asof,
        limit=limit,
        windowed=max_days_to_resolve is not None,
        min_win_multiple=min_win_multiple,
        min_win_probability=min_win_probability,
    )
    review = review_status_by_market(session, [market.id for _, _, market, _ in rows])
    sizes = suggested_size_by_estimate(
        session, [(market.id, estimate.id) for _, estimate, market, _ in rows]
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
            review_status=review[market.id],
            suggested_size=sizes[estimate.id],
        )
        for score, estimate, market, metrics in rows
    ]
