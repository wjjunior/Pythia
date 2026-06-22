"""Resolution trigger (§5, AC-4/15/16): record an observed outcome → settle → post-mortem."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from pythia.agents import ResolutionAgent, make_llm_port
from pythia.api.deps import BroadcasterDep, SessionDep, SettingsDep
from pythia.api.events import SseChannel
from pythia.api.schemas import ResolutionRequest, ResolutionResult
from pythia.common import ResolutionProposal
from pythia.db.repositories import MarketRepository, ResolutionRepository
from pythia.orchestration import ResolutionService
from pythia.paper import PaperLedger
from pythia.rag import EvidenceStore, make_embedder

router = APIRouter(tags=["resolutions"])


@router.post("/api/markets/{market_id}/resolve")
def resolve(
    market_id: uuid.UUID,
    body: ResolutionRequest,
    session: SessionDep,
    settings: SettingsDep,
    broadcaster: BroadcasterDep,
) -> ResolutionResult:
    mid = str(market_id)
    if MarketRepository(session).get(mid) is None:
        raise HTTPException(status_code=404, detail="market not found")
    if ResolutionRepository(session).exists(mid):
        raise HTTPException(status_code=409, detail="market already resolved")
    service = ResolutionService(
        session,
        settings,
        PaperLedger(session),
        on_calibration_changed=lambda category: broadcaster.publish(
            SseChannel.CALIBRATION_CHANGED, category
        ),
    )
    postmortem = service.record(
        mid,
        body.outcome,
        at=body.at or datetime.now(UTC),
        ambiguous=body.ambiguous,
        source=body.source,
    )
    return ResolutionResult(
        market_id=mid,
        outcome=body.outcome,
        ambiguous=body.ambiguous,
        postmortem_created=postmortem is not None,
    )


@router.post("/api/markets/{market_id}/resolution/propose")
def propose_resolution(
    market_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> ResolutionProposal:
    mid = str(market_id)
    market = MarketRepository(session).get(mid)
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")
    if ResolutionRepository(session).exists(mid):
        raise HTTPException(status_code=409, detail="market already resolved")
    agent = ResolutionAgent(
        make_llm_port(settings), EvidenceStore(session, make_embedder(settings))
    )
    return agent.propose(market, datetime.now(UTC))
