"""Health (§A.1): db reachability, crawler freshness (latest snapshot) and pipeline queue age."""

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import func, text
from sqlmodel import select

from pythia.api.deps import SessionDep
from pythia.api.schemas import HealthResponse
from pythia.db import models

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: SessionDep) -> HealthResponse:
    try:
        session.exec(select(text("1")))
    except Exception:
        return HealthResponse(
            status="degraded",
            database=False,
            last_snapshot_at=None,
            last_run_at=None,
            queue_age_seconds=None,
        )
    last_snapshot: datetime | None = session.exec(select(func.max(models.MarketSnapshot.ts))).one()
    last_run: datetime | None = session.exec(select(func.max(models.PipelineRun.started_at))).one()
    queue_age = (datetime.now(UTC) - last_run).total_seconds() if last_run is not None else None
    return HealthResponse(
        status="ok",
        database=True,
        last_snapshot_at=last_snapshot,
        last_run_at=last_run,
        queue_age_seconds=queue_age,
    )
