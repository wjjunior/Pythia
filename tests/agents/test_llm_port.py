"""Tests for the LLM gateway: the deterministic stub and the mocked OpenRouter path."""

import json
from datetime import UTC, datetime
from typing import Any

from pythia.agents import ModelRouter, OpenRouterLLMPort, StubLLMPort, make_llm_port
from pythia.common import (
    CostEvent,
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    LLMPort,
    Market,
    MarketSnapshot,
)
from pythia.config import Settings

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _ctx() -> EstimationContext:
    return EstimationContext(
        market=Market(
            id="m1",
            external_id="x",
            question="Will it rain?",
            category="weather",
            resolution_rule="rule",
        ),
        snapshot=MarketSnapshot(
            id="s1", market_id="m1", ts=_TS, yes_price=0.6, spread=0.02, volume=1.0, liquidity=1.0
        ),
        evidence=[
            Evidence(id="e1", market_id="m1", source_id="src1", observed_at=_TS, content="signal")
        ],
    )


def _settings(*, key: str | None = None) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y", openrouter_api_key=key)


def test_stub_estimate_is_deterministic_and_valid() -> None:
    port: LLMPort = StubLLMPort()
    estimate = port.estimate(_ctx())
    assert isinstance(estimate, Estimate)
    assert estimate.model_name == "stub"
    assert 0.0 <= estimate.p_model <= 1.0
    assert estimate.evidence_ids == ["e1"]
    assert StubLLMPort().estimate(_ctx()).p_model == estimate.p_model


def test_stub_critique_is_valid() -> None:
    port = StubLLMPort()
    verdict = port.critique(port.estimate(_ctx()), [])
    assert isinstance(verdict, CriticVerdict)


def test_make_llm_port_without_key_is_the_stub() -> None:
    assert isinstance(make_llm_port(_settings()), StubLLMPort)


def test_make_llm_port_with_key_is_openrouter() -> None:
    assert isinstance(make_llm_port(_settings(key="sk-test")), OpenRouterLLMPort)


class _FakeTransport:
    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "max_price": max_price})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _estimate_response(model: str = "minimax/minimax-m3", cost: float = 0.0021) -> dict[str, Any]:
    content = json.dumps(
        {"p_model": 0.62, "confidence": 0.55, "lower": 0.5, "upper": 0.72, "rationale": "because"}
    )
    return {
        "model": model,
        "provider": "minimax",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "cost": cost},
    }


def test_openrouter_estimate_parses_and_emits_measured_cost() -> None:
    costs: list[CostEvent] = []
    transport = _FakeTransport([_estimate_response()])
    router = ModelRouter(slates={"estimation": ["minimax/minimax-m3"]}, max_price=0.5)
    port = OpenRouterLLMPort(transport, router, cost_sink=costs.append)
    estimate = port.estimate(_ctx())
    assert estimate.p_model == 0.62
    assert estimate.model_name == "minimax/minimax-m3"
    assert estimate.evidence_ids == ["e1"]
    assert len(costs) == 1
    assert costs[0].cost_usd == 0.0021
    assert costs[0].agent == "estimation"
    assert costs[0].input_tokens == 1200
    assert transport.calls[0]["max_price"] == 0.5


def test_openrouter_fails_over_to_the_next_model_on_error() -> None:
    transport = _FakeTransport(
        [RuntimeError("provider down"), _estimate_response(model="google/gemini-3-flash")]
    )
    router = ModelRouter(slates={"estimation": ["a/first", "google/gemini-3-flash"]})
    estimate = OpenRouterLLMPort(transport, router).estimate(_ctx())
    assert estimate.model_name == "google/gemini-3-flash"
    assert len(transport.calls) == 2


def test_openrouter_estimate_handles_markdown_fenced_json() -> None:
    fenced = (
        "```json\n"
        + json.dumps(
            {"p_model": 0.4, "confidence": 0.5, "lower": 0.3, "upper": 0.5, "rationale": "r"}
        )
        + "\n```"
    )
    response: dict[str, Any] = {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": fenced}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0},
    }
    estimate = OpenRouterLLMPort(_FakeTransport([response]), ModelRouter()).estimate(_ctx())
    assert estimate.p_model == 0.4
