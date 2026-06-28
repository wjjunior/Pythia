"""Pipeline trigger endpoint (§2.3, AC-6): run estimation on open markets, nudge the radar."""

from fastapi import APIRouter

from pythia.api.deps import BroadcasterDep, SessionDep, SettingsDep
from pythia.api.events import SseChannel
from pythia.api.schemas import RunResponse
from pythia.orchestration import make_funnel_sink, run_open_markets

router = APIRouter(tags=["run"])


@router.post("/api/run")
def run(session: SessionDep, settings: SettingsDep, broadcaster: BroadcasterDep) -> RunResponse:
    summary = run_open_markets(
        session,
        settings,
        publish_radar=lambda market_id: broadcaster.publish(SseChannel.RADAR_CHANGED, market_id),
        funnel_sink=make_funnel_sink(session),
    )
    return RunResponse(
        markets=summary.markets, scored=summary.scored, halted=summary.halted, arbs=summary.arbs
    )
