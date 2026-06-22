"""Tests for the evaluation harness: metrics, ranking and per-agent ablation (§5.2)."""

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from pythia.agents import ModelRouter, OpenRouterLLMPort, StubLLMPort
from pythia.common import (
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    Market,
    MarketSnapshot,
)
from pythia.eval.harness import (
    CaseResult,
    EvalCase,
    HarnessConfig,
    Runner,
    ablate,
    brier_per_dollar,
    config_report,
    llm_runner,
    run_harness,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _ctx(q: float) -> EstimationContext:
    return EstimationContext(
        market=Market(
            id="m", external_id="x", question="q", category="politics", resolution_rule="r"
        ),
        snapshot=MarketSnapshot(
            id="s", market_id="m", ts=_TS, yes_price=q, spread=0.02, volume=1.0, liquidity=1.0
        ),
        evidence=[],
    )


def _case(q: float, outcome: int) -> EvalCase:
    return EvalCase(
        market_id="m", category="politics", context=_ctx(q), p_market=q, outcome=outcome
    )


_DATASET = [_case(0.5, 1), _case(0.5, 0)]


def _fixed_runner(p_model: Callable[[EvalCase], float], cost: float) -> Runner:
    def runner(case: EvalCase) -> CaseResult:
        return CaseResult(p_model=p_model(case), cost_usd=cost, latency_ms=5.0)

    return runner


def test_brier_per_dollar_is_improvement_over_cost() -> None:
    assert brier_per_dollar(0.25, 0.2) == pytest.approx(1.25)


def test_brier_per_dollar_handles_free_configs() -> None:
    assert brier_per_dollar(0.1, 0.0) == math.inf
    assert brier_per_dollar(0.0, 0.0) == 0.0
    assert brier_per_dollar(-0.1, 0.0) == -math.inf


def test_config_report_computes_brier_delta_and_n() -> None:
    results = [
        CaseResult(p_model=1.0, cost_usd=0.1, latency_ms=4.0),
        CaseResult(p_model=0.0, cost_usd=0.1, latency_ms=6.0),
    ]
    report = config_report("accurate", _DATASET, results)
    assert report.brier_system == pytest.approx(0.0)
    assert report.brier_market == pytest.approx(0.25)
    assert report.brier_delta == pytest.approx(0.25)
    assert report.total_cost_usd == pytest.approx(0.2)
    assert report.brier_per_dollar == pytest.approx(1.25)
    assert report.mean_latency_ms == pytest.approx(5.0)
    assert report.error_rate == 0.0
    assert report.n == 2
    assert report.by_category[0].category == "politics"


def test_run_harness_ranks_by_brier_per_dollar() -> None:
    accurate = HarnessConfig("accurate", _fixed_runner(lambda c: float(c.outcome), cost=0.1))
    cheap = HarnessConfig("cheap", _fixed_runner(lambda c: c.p_market, cost=0.01))
    report = run_harness(_DATASET, [cheap, accurate])
    assert [report.name for report in report.configs] == ["accurate", "cheap"]


def test_errored_cases_are_excluded_from_brier_but_counted() -> None:
    results = [
        CaseResult(p_model=1.0, cost_usd=0.1, latency_ms=4.0),
        CaseResult(p_model=0.0, cost_usd=0.1, latency_ms=6.0, errored=True),
    ]
    report = config_report("x", _DATASET, results)
    assert report.n == 1
    assert report.error_rate == pytest.approx(0.5)


def test_ablation_swaps_one_model_and_reports_the_delta() -> None:
    base = HarnessConfig("base", _fixed_runner(lambda c: c.p_market, cost=0.05))
    variant = HarnessConfig("variant", _fixed_runner(lambda c: float(c.outcome), cost=0.05))
    result = ablate(_DATASET, base, variant)
    assert result.brier_delta_change == pytest.approx(0.25)
    assert result.brier_per_dollar_delta > 0.0


def test_llm_runner_with_stub_produces_a_report_offline() -> None:
    config = HarnessConfig("stub", llm_runner(lambda sink: StubLLMPort()))
    report = run_harness(_DATASET, [config])
    stub = report.configs[0]
    assert stub.n == 2
    assert stub.error_rate == 0.0
    assert stub.total_cost_usd == 0.0


class _BrokenLLM:
    def estimate(self, ctx: EstimationContext) -> Estimate:
        raise RuntimeError("boom")

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        raise NotImplementedError

    def discover(self, markets: list[Market], limit: int) -> list[str]:
        raise NotImplementedError


def test_llm_runner_counts_estimation_failures_as_errors() -> None:
    config = HarnessConfig("broken", llm_runner(lambda sink: _BrokenLLM()))
    report = run_harness(_DATASET, [config])
    assert report.configs[0].error_rate == 1.0
    assert report.configs[0].n == 0


class _ByModelTransport:
    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        return self._responses[model]


def _estimation_response(p: float, cost: float) -> dict[str, Any]:
    content = json.dumps(
        {"p_model": p, "confidence": 0.6, "lower": 0.4, "upper": 0.7, "rationale": "r"}
    )
    return {
        "provider": "prov",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost},
    }


def _critique_response(cost: float) -> dict[str, Any]:
    content = json.dumps(
        {"adjusted_confidence": 0.5, "objections": [], "lookahead_leak": False, "notes": "ok"}
    )
    return {
        "provider": "prov",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": cost},
    }


def test_single_best_vs_ensemble_is_a_ranked_harness_axis() -> None:
    responses = {
        "a/m1": _estimation_response(0.6, cost=0.01),
        "b/m2": _estimation_response(0.5, cost=0.01),
        "c/m3": _estimation_response(0.4, cost=0.01),
        "k/crit": _critique_response(cost=0.005),
    }
    slates = {"estimation": ["a/m1", "b/m2", "c/m3"], "critic": ["k/crit"]}
    static_router = ModelRouter(slates=slates, strategy="static")
    ensemble_router = ModelRouter(slates=slates, strategy="ensemble")

    def build(
        router: ModelRouter, size: int
    ) -> Callable[[Callable[[Any], None]], OpenRouterLLMPort]:
        return lambda sink: OpenRouterLLMPort(
            _ByModelTransport(responses), router, cost_sink=sink, ensemble_size=size
        )

    single = HarnessConfig("single", llm_runner(build(static_router, 1)))
    ensemble = HarnessConfig("ensemble", llm_runner(build(ensemble_router, 3)))
    report = run_harness(_DATASET, [single, ensemble])

    costs = {config.name: config.total_cost_usd for config in report.configs}
    assert set(costs) == {"single", "ensemble"}
    assert costs["ensemble"] > costs["single"]
    ranked = [config.brier_per_dollar for config in report.configs]
    assert ranked == sorted(ranked, reverse=True)
