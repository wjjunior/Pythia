"""Pipeline runner composition root on real pgvector (§2.3, AC-6/AC-8, PYT-41)."""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session, select

from pythia.agents import BudgetGuard, CachingLLMPort, ModelRouter, OpenRouterLLMPort
from pythia.common import (
    CostEvent,
    EstimationContext,
    LLMPort,
    Market,
    MarketSnapshot,
    Source,
)
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    CostRepository,
    MarketRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.orchestration.runner import run_open_markets
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.worker.scheduler import pipeline_tick

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt41-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt41-snapshot"))
SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt41-source"))
ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _seed_open_market(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt41",
            question="Will candidate Alpha win the 2026 governor election?",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=ASOF,
            yes_price=0.5,
            spread=0.02,
            volume=1000.0,
            liquidity=5000.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_ID, name="reuters", kind="news", reliability=0.9)
    )
    EvidenceStore(session, StubEmbeddingPort()).add(
        market_id=MARKET_ID,
        source_id=SOURCE_ID,
        observed_at=ASOF - timedelta(days=1),
        content="Polls show candidate Alpha leading the 2026 governor election.",
    )


def test_run_open_markets_scores_and_publishes_radar(session: Session) -> None:
    _seed_open_market(session)
    published: list[str] = []

    summary = run_open_markets(session, _settings(), publish_radar=published.append)

    assert summary.markets == 1
    assert summary.scored == 1
    assert summary.halted is False
    assert published == [MARKET_ID]
    assert len(session.exec(select(models.Score)).all()) == 1


def test_run_open_markets_does_not_re_estimate_an_already_estimated_snapshot(
    session: Session,
) -> None:
    _seed_open_market(session)
    run_open_markets(session, _settings())
    runs = len(session.exec(select(models.PipelineRun)).all())
    estimates = len(session.exec(select(models.Estimate)).all())
    assert runs == 1 and estimates == 1

    run_open_markets(session, _settings())

    assert len(session.exec(select(models.PipelineRun)).all()) == runs
    assert len(session.exec(select(models.Estimate)).all()) == estimates


def test_run_open_markets_skips_markets_without_a_snapshot(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt41",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    published: list[str] = []

    summary = run_open_markets(session, _settings(), publish_radar=published.append)

    assert summary.scored == 0
    assert published == []


def test_pipeline_tick_scores_open_markets(session: Session) -> None:
    _seed_open_market(session)

    @contextmanager
    def factory() -> Iterator[Session]:
        yield session

    pipeline_tick(factory, _settings())

    assert len(session.exec(select(models.Score)).all()) >= 1


class _CountingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        self.calls += 1
        content = json.dumps(
            {"p_model": 0.6, "confidence": 0.5, "lower": 0.5, "upper": 0.7, "rationale": "r"}
        )
        return {
            "model": model,
            "provider": "fake",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.02},
        }


def _cost(cost: float) -> CostEvent:
    return CostEvent(
        agent="estimation",
        task="estimate",
        provider="fake",
        model="m",
        input_tokens=10,
        output_tokens=5,
        cost_usd=cost,
    )


def _ctx() -> EstimationContext:
    return EstimationContext(
        market=Market(
            id=MARKET_ID, external_id="x", question="q", category="politics", resolution_rule="r"
        ),
        snapshot=MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=ASOF,
            yes_price=0.6,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        ),
        evidence=[],
    )


def _guarded_llm(session: Session, market_id: str, *, run_cap: float, daily_cap: float) -> LLMPort:
    guard = BudgetGuard(
        CostRepository(session), run_cap=run_cap, daily_cap=daily_cap, market_id=market_id
    )
    return CachingLLMPort(
        OpenRouterLLMPort(
            _CountingTransport(), ModelRouter(), cost_sink=guard.record, budget_check=guard.check
        )
    )


def test_run_open_markets_halts_when_the_daily_budget_is_breached(session: Session) -> None:
    _seed_open_market(session)
    settings = _settings()
    CostRepository(session).record(_cost(settings.cost_budget_daily + 1.0))

    def factory(market_id: str) -> LLMPort:
        return _guarded_llm(
            session,
            market_id,
            run_cap=settings.cost_cap_per_run,
            daily_cap=settings.cost_budget_daily,
        )

    summary = run_open_markets(session, settings, llm_factory=factory)

    assert summary.halted is True
    assert summary.scored == 0


def test_run_open_markets_attributes_pipeline_cost_to_the_market_category(
    session: Session,
) -> None:
    _seed_open_market(session)  # category: politics

    def factory(market_id: str) -> LLMPort:
        return _guarded_llm(session, market_id, run_cap=100.0, daily_cap=100.0)

    run_open_markets(session, _settings(), llm_factory=factory)

    assert CostRepository(session).by_category().get("politics", 0.0) > 0.0


def test_run_open_markets_attributes_cost_to_the_pipeline_run(session: Session) -> None:
    _seed_open_market(session)

    def factory(market_id: str) -> LLMPort:
        return _guarded_llm(session, market_id, run_cap=100.0, daily_cap=100.0)

    run_open_markets(session, _settings(), llm_factory=factory)

    runs = session.exec(select(models.PipelineRun)).all()
    assert len(runs) == 1
    assert CostRepository(session).run_total(str(runs[0].id)) > 0.0


def test_cache_prevents_rebilling_an_identical_estimate_on_the_live_path(session: Session) -> None:
    guard = BudgetGuard(CostRepository(session), run_cap=100.0, daily_cap=100.0)
    transport = _CountingTransport()
    llm = CachingLLMPort(
        OpenRouterLLMPort(
            transport, ModelRouter(), cost_sink=guard.record, budget_check=guard.check
        )
    )

    first = llm.estimate(_ctx())
    second = llm.estimate(_ctx())

    assert first is second
    assert transport.calls == 1
    assert CostRepository(session).total() == pytest.approx(0.02)


def _seed_open_markets(session: Session, count: int) -> None:
    for index in range(count):
        market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt59-market-{index}"))
        MarketRepository(session).upsert(
            Market(
                id=market_id,
                external_id=f"pyt59-{index}",
                question="q",
                category="politics",
                resolution_rule="r",
            )
        )
        SnapshotRepository(session).append(
            MarketSnapshot(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt59-snap-{index}")),
                market_id=market_id,
                ts=ASOF,
                yes_price=0.5,
                spread=0.02,
                volume=1.0,
                liquidity=1.0,
            )
        )


def _discovery_settings(size: int) -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql+psycopg://x/y", discovery_shortlist_size=size
    )


def test_run_open_markets_should_shortlist_when_discovery_size_is_set(session: Session) -> None:
    _seed_open_markets(session, 3)
    summary = run_open_markets(session, _discovery_settings(2))
    assert summary.markets == 2


def test_run_open_markets_should_process_all_when_discovery_is_off(session: Session) -> None:
    _seed_open_markets(session, 3)
    summary = run_open_markets(session, _settings())
    assert summary.markets == 3


def test_run_open_markets_should_ignore_a_non_positive_discovery_size(session: Session) -> None:
    _seed_open_markets(session, 3)
    summary = run_open_markets(session, _discovery_settings(0))
    assert summary.markets == 3


def _estimated_market_ids(session: Session) -> set[str]:
    return {str(mid) for mid in session.exec(select(models.Estimate.market_id)).all()}


def test_shortlist_cursor_rotates_estimation_across_ticks(session: Session) -> None:
    # 5 open markets, 2 estimated per tick: the cursor advances each tick so the *set* of estimated
    # markets grows through the whole open set, not the same top-2 re-picked every run (PYT-165).
    _seed_open_markets(session, 5)
    settings = _discovery_settings(2)

    run_open_markets(session, settings)
    assert len(_estimated_market_ids(session)) == 2  # only 2 estimated on the first tick

    run_open_markets(session, settings)
    run_open_markets(session, settings)

    assert len(_estimated_market_ids(session)) == 5  # three ticks rotate through all five markets


def _seed_priced_market(session: Session, name: str, price: float) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
    MarketRepository(session).upsert(
        Market(
            id=market_id, external_id=name, question="q", category="politics", resolution_rule="r"
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}-snap")),
            market_id=market_id,
            ts=ASOF,
            yes_price=price,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return market_id


def test_shortlist_prioritizes_midband_before_near_certain_markets(session: Session) -> None:
    # 2 midband (q=0.5) + 2 near-certain (q=0.97), 2 estimated/tick: the midband markets — where
    # calibration is measurable — are estimated first; the tails wait for the next tick (PYT-167).
    midband = {_seed_priced_market(session, f"mid{i}", 0.5) for i in range(2)}
    tails = {_seed_priced_market(session, f"tail{i}", 0.97) for i in range(2)}
    settings = _discovery_settings(2)

    run_open_markets(session, settings)
    assert _estimated_market_ids(session) == midband  # midband first, tails not yet

    run_open_markets(session, settings)
    assert _estimated_market_ids(session) == midband | tails  # tails reached next — not excluded
