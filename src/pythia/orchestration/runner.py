"""Composition root: run the estimation pipeline on open markets (§2.3, AC-6/AC-8).

The only place outside tests that builds a `Pipeline`. It arms the cost guards from settings —
a per-market `BudgetGuard` (per-run cap + daily kill switch, tagging cost events with `market_id`)
and the `(market, snapshot, evidence_hash)` re-bill cache wrapping `make_llm_port` — and runs the
pipeline over every open market's latest snapshot. A score nudges the radar via `publish_radar`;
a breached budget halts the batch.
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

# market_id -> the LLMPort used for that market's pipeline run.
LlmFactory = Callable[[str], LLMPort]


@dataclass(frozen=True, slots=True)
class RunSummary:
    markets: int
    scored: int
    halted: bool


def _default_llm_factory(session: Session, settings: Settings) -> LlmFactory:
    def build(market_id: str) -> LLMPort:
        guard = BudgetGuard(
            CostRepository(session),
            run_cap=settings.cost_cap_per_run,
            daily_cap=settings.cost_budget_daily,
            market_id=market_id,
        )
        return CachingLLMPort(
            make_llm_port(settings, cost_sink=guard.record, budget_check=guard.check)
        )

    return build


def _routing(settings: Settings) -> dict[str, str]:
    selection = settings.model_routing_strategy if settings.llm_enabled else "stub"
    return {"estimation": selection, "critic": selection}


def run_open_markets(
    session: Session,
    settings: Settings,
    *,
    publish_radar: Callable[[str], None] | None = None,
    llm_factory: LlmFactory | None = None,
) -> RunSummary:
    markets = MarketRepository(session).list(status=MarketStatus.OPEN)
    snapshots = SnapshotRepository(session)
    evidence = EvidenceStore(session, make_embedder(settings))
    build_llm = llm_factory if llm_factory is not None else _default_llm_factory(session, settings)
    routing = _routing(settings)
    scored = 0
    for market in markets:
        snapshot = snapshots.latest_for_market(market.id)
        if snapshot is None:
            continue
        pipeline = Pipeline(
            session=session,
            llm=build_llm(market.id),
            evidence=evidence,
            settings=settings,
            routing=routing,
        )
        try:
            result = pipeline.run(market, snapshot)
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
