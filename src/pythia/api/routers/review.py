"""Human review gate endpoint (§A.1): approve → paper trade, reject → terminal."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from pythia.api.deps import BroadcasterDep, SessionDep, SettingsDep
from pythia.api.events import SseChannel
from pythia.api.schemas import ReviewAction, ReviewRequest, ReviewResult
from pythia.config.settings import ExecutionModeError
from pythia.orchestration import (
    NotRiskEvaluatedError,
    NotScoredError,
    ReviewGate,
    UnknownEstimateError,
    VetoedOpportunityError,
)
from pythia.paper import PaperLedger

router = APIRouter(tags=["review"])


@router.post("/api/review/{estimate_id}")
def review(
    estimate_id: uuid.UUID,
    body: ReviewRequest,
    session: SessionDep,
    settings: SettingsDep,
    broadcaster: BroadcasterDep,
) -> ReviewResult:
    estimate = str(estimate_id)
    gate = ReviewGate(session, PaperLedger(session), settings)
    try:
        if body.action == ReviewAction.REJECT:
            gate.reject(estimate, reviewer=body.reviewer)
            return ReviewResult(estimate_id=estimate, action=body.action, trade=None)
        if body.side is None or body.size is None or body.size <= 0.0:
            raise HTTPException(status_code=422, detail="approve requires side and a positive size")
        trade = gate.approve(
            estimate,
            side=body.side,
            size=body.size,
            at=datetime.now(UTC),
            reviewer=body.reviewer,
        )
    except UnknownEstimateError:
        raise HTTPException(status_code=404, detail="estimate not found") from None
    except NotScoredError:
        raise HTTPException(status_code=409, detail="estimate is not scored") from None
    except NotRiskEvaluatedError:
        raise HTTPException(status_code=409, detail="estimate has no risk decision") from None
    except VetoedOpportunityError:
        raise HTTPException(status_code=409, detail="opportunity vetoed by the risk gate") from None
    except ExecutionModeError:
        raise HTTPException(status_code=409, detail="not in paper mode") from None
    broadcaster.publish(SseChannel.PAPER_CHANGED, trade.id)
    return ReviewResult(estimate_id=estimate, action=body.action, trade=trade)
