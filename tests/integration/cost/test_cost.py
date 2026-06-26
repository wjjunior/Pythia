"""Cost persistence, aggregation and the budget kill switch against real Postgres (§5.3)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session, select

from pythia.agents import BudgetExceeded, BudgetGuard, ModelRouter, OpenRouterLLMPort
from pythia.common import CostEvent, EstimationContext, Market, MarketSnapshot
from pythia.common.logging import bind_correlation, clear_correlation
from pythia.db import models
from pythia.db.repositories import CostRepository

pytestmark = pytest.mark.integration

RUN_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt24-run"))
MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt24-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt24-snapshot"))
_TS = datetime(2026, 3, 1, tzinfo=UTC)


def _cost(*, agent: str = "estimation", model: str = "m/x", cost: float = 0.01) -> CostEvent:
    return CostEvent(
        agent=agent,
        task="estimate",
        provider="openrouter",
        model=model,
        input_tokens=10,
        output_tokens=5,
        cost_usd=cost,
    )


def test_record_and_aggregate_costs(session: Session) -> None:
    repo = CostRepository(session)
    repo.record(_cost(agent="estimation", model="a", cost=0.02))
    repo.record(_cost(agent="estimation", model="b", cost=0.03))
    repo.record(_cost(agent="critic", model="a", cost=0.05))

    assert repo.total() == pytest.approx(0.10)
    assert repo.by_agent() == pytest.approx({"estimation": 0.05, "critic": 0.05})
    assert repo.by_model() == pytest.approx({"a": 0.07, "b": 0.03})

    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    assert repo.daily_total(start_of_day) == pytest.approx(0.10)
    assert repo.daily_total(datetime.now(UTC) + timedelta(days=1)) == pytest.approx(0.0)


def test_by_category_groups_cost_through_the_market(session: Session) -> None:
    politics = uuid.uuid5(uuid.NAMESPACE_URL, "pyt44-politics")
    sports = uuid.uuid5(uuid.NAMESPACE_URL, "pyt44-sports")
    session.add(
        models.Market(
            id=politics,
            external_id="pyt44-p",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    session.add(
        models.Market(
            id=sports, external_id="pyt44-s", question="q", category="sports", resolution_rule="r"
        )
    )
    session.commit()

    repo = CostRepository(session)
    repo.record(_cost(cost=0.02), market_id=str(politics))
    repo.record(_cost(cost=0.03), market_id=str(politics))
    repo.record(_cost(cost=0.05), market_id=str(sports))
    repo.record(_cost(cost=0.99))

    assert repo.by_category() == pytest.approx({"politics": 0.05, "sports": 0.05})


def test_run_total_only_counts_the_runs_costs(session: Session) -> None:
    session.add(models.PipelineRun(id=uuid.UUID(RUN_ID), routing={}, status="running"))
    session.commit()
    repo = CostRepository(session)
    repo.record(_cost(cost=0.04), pipeline_run_id=RUN_ID)
    repo.record(_cost(cost=0.06), pipeline_run_id=RUN_ID)
    repo.record(_cost(cost=0.99))

    assert repo.run_total(RUN_ID) == pytest.approx(0.10)


def test_cost_per_scored_opportunity(session: Session) -> None:
    session.add(
        models.Market(
            id=uuid.UUID(MARKET_ID),
            external_id="pyt24",
            question="q",
            category="c",
            resolution_rule="r",
        )
    )
    session.add(
        models.MarketSnapshot(
            id=uuid.UUID(SNAPSHOT_ID),
            market_id=uuid.UUID(MARKET_ID),
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    session.commit()
    for index in range(2):
        estimate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pyt24-estimate-{index}")
        session.add(
            models.Estimate(
                id=estimate_id,
                market_id=uuid.UUID(MARKET_ID),
                snapshot_id=uuid.UUID(SNAPSHOT_ID),
                p_model=0.5,
                confidence=0.5,
                lower=0.4,
                upper=0.6,
                rationale="r",
                evidence_ids=[],
                model_name="stub",
            )
        )
        session.commit()
        session.add(
            models.Score(
                estimate_id=estimate_id,
                edge=0.0,
                edge_net=0.0,
                ev_unit=0.0,
                p_resol=1.0,
                p_liq=1.0,
                p_time=1.0,
                p_corr=1.0,
                opportunity_score=0.0,
                kelly_fraction=0.0,
            )
        )
        session.commit()

    repo = CostRepository(session)
    repo.record(_cost(cost=0.10))
    assert repo.cost_per_scored_opportunity() == pytest.approx(0.05)


class _FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.calls = 0
        self._response = response

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        self.calls += 1
        return self._response


def _estimate_response(cost: float) -> dict[str, Any]:
    content = json.dumps(
        {"p_model": 0.6, "confidence": 0.5, "lower": 0.5, "upper": 0.7, "rationale": "r"}
    )
    return {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost},
    }


def _ctx() -> EstimationContext:
    return EstimationContext(
        market=Market(
            id=MARKET_ID, external_id="x", question="q", category="c", resolution_rule="r"
        ),
        snapshot=MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=_TS,
            yes_price=0.6,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        ),
        evidence=[],
    )


def test_per_run_cap_halts_the_next_call(session: Session) -> None:
    guard = BudgetGuard(CostRepository(session), run_cap=0.5, daily_cap=1000.0)
    transport = _FakeTransport(_estimate_response(0.6))
    port = OpenRouterLLMPort(
        transport, ModelRouter(), cost_sink=guard.record, budget_check=guard.check
    )

    port.estimate(_ctx())
    with pytest.raises(BudgetExceeded):
        port.estimate(_ctx())
    assert transport.calls == 1


def test_daily_budget_cap_halts_calls(session: Session) -> None:
    repo = CostRepository(session)
    repo.record(_cost(cost=0.05))
    guard = BudgetGuard(repo, run_cap=1000.0, daily_cap=0.05)
    with pytest.raises(BudgetExceeded):
        guard.check()


def test_should_persist_all_four_cost_tags_from_the_adapter_path(session: Session) -> None:
    # F-10: the four tags must reach cost_events from the adapter's response path, not a hand-built
    # row. A regression dropping any tag in _record_call must fail this test.
    correlation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt128-corr"))
    bind_correlation(correlation_id)
    try:
        port = OpenRouterLLMPort(
            _FakeTransport(_estimate_response(0.01)),
            ModelRouter(),
            cost_sink=CostRepository(session).record,
        )
        port.estimate(_ctx())
    finally:
        clear_correlation()

    rows = session.exec(select(models.CostEvent)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.agent == "estimation"  # the calling agent, from the adapter
    assert row.provider == "p"  # from the response, not the "openrouter" default
    assert row.model  # router-selected model, populated
    assert row.correlation_id == correlation_id  # bound run context, threaded through _record_call
