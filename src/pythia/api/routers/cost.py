"""Cost panel (§A.1, §5.3): measured spend by agent / model / day and cost per opportunity."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep
from pythia.api.schemas import CostResponse
from pythia.db.repositories import CostRepository

router = APIRouter(tags=["cost"])


@router.get("/api/cost")
def cost(session: SessionDep) -> CostResponse:
    repo = CostRepository(session)
    return CostResponse(
        total=repo.total(),
        cost_per_opportunity=repo.cost_per_scored_opportunity(),
        by_agent=repo.by_agent(),
        by_model=repo.by_model(),
        by_day=repo.by_day(),
    )
