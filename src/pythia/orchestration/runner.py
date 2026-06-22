"""Composition root: run the estimation pipeline on open markets (§2.3, AC-6/AC-8).

The only place outside tests that builds a `Pipeline`. It arms the cost guards from settings —
`BudgetGuard` (per-run + daily kill switch) and the `(market, snapshot, evidence_hash)` re-bill
cache — wires them into the live `LLMPort`, and runs the pipeline over every open market's latest
snapshot. A score nudges the radar via `publish_radar`; a breached budget halts the batch.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session

from pythia.agents import BudgetExceeded, BudgetGuard, CachingLLMPort, make_llm_port
from pythia.common import LLMPort, MarketStatus
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import CostRepository, MarketRepository, SnapshotRepository
from pythia.orchestration.pipeline import Pipeline
from pythia.rag import EvidenceStore, make_embedder

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RunSummary:
    markets: int
    scored: int
    halted: bool


def _build_guarded_llm(session: Session, settings: Settings) -> LLMPort:
    guard = BudgetGuard(
        CostRepository(session),
        run_cap=settings.cost_cap_per_run,
        daily_cap=settings.cost_budget_daily,
    )
    return CachingLLMPort(make_llm_port(settings, cost_sink=guard.record, budget_check=guard.check))


def run_open_markets(
    session: Session,
    settings: Settings,
    *,
    publish_radar: Callable[[str], None] | None = None,
    llm: LLMPort | None = None,
) -> RunSummary:
    markets = MarketRepository(session).list(status=MarketStatus.OPEN)
    snapshots = SnapshotRepository(session)
    routing = {"estimation": settings.llm_model or "stub", "critic": settings.llm_model or "stub"}
    pipeline = Pipeline(
        session=session,
        llm=llm if llm is not None else _build_guarded_llm(session, settings),
        evidence=EvidenceStore(session, make_embedder(settings)),
        settings=settings,
        routing=routing,
    )
    scored = 0
    for market in markets:
        history = snapshots.list_for_market(market.id)
        if not history:
            continue
        try:
            result = pipeline.run(market, history[-1])
        except BudgetExceeded:
            _log.warning("pipeline_budget_halt", market_id=market.id)
            return RunSummary(markets=len(markets), scored=scored, halted=True)
        except Exception:
            _log.exception("pipeline_market_failed", market_id=market.id)
            continue
        if result.score is not None:
            scored += 1
            if publish_radar is not None:
                publish_radar(market.id)
    return RunSummary(markets=len(markets), scored=scored, halted=False)
