"""Tests for the LLM gateway: the deterministic stub and the mocked OpenRouter path."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from pythia.agents import (
    HttpxChatTransport,
    LLMResponseError,
    ModelRouter,
    OpenRouterLLMPort,
    StubLLMPort,
    make_llm_port,
)
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
from pythia.common.logging import bind_correlation, clear_correlation
from pythia.config import Settings

_TS = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_correlation() -> Iterator[None]:
    yield
    clear_correlation()


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


def test_httpx_transport_requests_json_object_response_format() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    transport = HttpxChatTransport("k", "https://example.test")
    transport._client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    transport.complete(model="m", messages=[{"role": "user", "content": "x"}], max_price=None)

    assert seen["body"]["response_format"] == {"type": "json_object"}


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


def test_estimate_emits_llm_call_log_with_cost_fields() -> None:
    transport = _FakeTransport([_estimate_response()])
    router = ModelRouter(slates={"estimation": ["minimax/minimax-m3"]}, max_price=0.5)
    port = OpenRouterLLMPort(transport, router)
    with capture_logs() as logs:
        port.estimate(_ctx())
    call = next(entry for entry in logs if entry["event"] == "llm_call")
    assert call["agent"] == "estimation"
    assert call["task"] == "estimate"
    assert call["model"] == "minimax/minimax-m3"
    assert call["cost_usd"] == 0.0021
    assert call["input_tokens"] == 1200
    assert call["output_tokens"] == 300
    assert "latency_ms" in call


def test_cost_event_is_stamped_with_the_bound_correlation_id() -> None:
    costs: list[CostEvent] = []
    transport = _FakeTransport([_estimate_response()])
    router = ModelRouter(slates={"estimation": ["minimax/minimax-m3"]})
    port = OpenRouterLLMPort(transport, router, cost_sink=costs.append)
    bind_correlation("run-xyz")
    port.estimate(_ctx())
    assert costs[0].correlation_id == "run-xyz"


def test_bound_correlation_id_overrides_the_constructor_value() -> None:
    costs: list[CostEvent] = []
    transport = _FakeTransport([_estimate_response()])
    router = ModelRouter(slates={"estimation": ["minimax/minimax-m3"]})
    port = OpenRouterLLMPort(transport, router, cost_sink=costs.append, correlation_id="ctor-id")
    bind_correlation("run-bound")
    port.estimate(_ctx())
    assert costs[0].correlation_id == "run-bound"


class _ByModelTransport:
    def __init__(self, responses: dict[str, dict[str, Any] | Exception]) -> None:
        self.calls: list[str] = []
        self._responses = responses

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        self.calls.append(model)
        item = self._responses[model]
        if isinstance(item, Exception):
            raise item
        return item


def _member_response(p: float, *, cost: float = 0.001) -> dict[str, Any]:
    content = json.dumps(
        {"p_model": p, "confidence": 0.5, "lower": 0.4, "upper": 0.6, "rationale": "r"}
    )
    return {
        "model": "m",
        "provider": "prov",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost},
    }


def test_ensemble_runs_distinct_family_members_and_aggregates_the_median() -> None:
    costs: list[CostEvent] = []
    transport = _ByModelTransport(
        {
            "a/m1": _member_response(0.2),
            "b/m2": _member_response(0.5),
            "c/m3": _member_response(0.9),
        }
    )
    router = ModelRouter(slates={"estimation": ["a/m1", "b/m2", "c/m3"]}, strategy="ensemble")
    port = OpenRouterLLMPort(transport, router, cost_sink=costs.append, ensemble_size=3)
    estimate = port.estimate(_ctx())
    assert estimate.model_name == "ensemble"
    assert estimate.p_model == 0.5
    assert {member.model for member in estimate.members} == {"a/m1", "b/m2", "c/m3"}
    assert len(costs) == 3


def test_ensemble_drops_a_member_that_errors() -> None:
    transport = _ByModelTransport(
        {
            "a/m1": _member_response(0.4),
            "b/m2": RuntimeError("provider down"),
            "c/m3": _member_response(0.6),
        }
    )
    router = ModelRouter(slates={"estimation": ["a/m1", "b/m2", "c/m3"]}, strategy="ensemble")
    estimate = OpenRouterLLMPort(transport, router, ensemble_size=3).estimate(_ctx())
    assert {member.model for member in estimate.members} == {"a/m1", "c/m3"}
    assert estimate.p_model == pytest.approx(0.5)


def test_ensemble_logs_a_dropped_member() -> None:
    transport = _ByModelTransport(
        {
            "a/m1": _member_response(0.4),
            "b/m2": RuntimeError("provider down"),
            "c/m3": _member_response(0.6),
        }
    )
    router = ModelRouter(slates={"estimation": ["a/m1", "b/m2", "c/m3"]}, strategy="ensemble")
    with capture_logs() as logs:
        OpenRouterLLMPort(transport, router, ensemble_size=3).estimate(_ctx())
    dropped = [entry for entry in logs if entry["event"] == "ensemble_member_dropped"]
    assert [entry["model"] for entry in dropped] == ["b/m2"]


def test_ensemble_size_one_keeps_the_single_model_path() -> None:
    transport = _FakeTransport([_estimate_response()])
    router = ModelRouter(slates={"estimation": ["minimax/minimax-m3"]})
    estimate = OpenRouterLLMPort(transport, router, ensemble_size=1).estimate(_ctx())
    assert estimate.members == []
    assert estimate.model_name == "minimax/minimax-m3"


def _conf_response(confidence: float, *, cost: float = 0.001) -> dict[str, Any]:
    content = json.dumps(
        {"p_model": 0.6, "confidence": confidence, "lower": 0.4, "upper": 0.6, "rationale": "r"}
    )
    return {
        "model": "m",
        "provider": "prov",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": cost},
    }


def _escalate_router() -> ModelRouter:
    return ModelRouter(
        slates={"estimation": ["cheap/m1", "mid/m2", "strong/m3"]}, strategy="escalate"
    )


def test_escalate_stops_at_the_first_confident_model() -> None:
    transport = _ByModelTransport({"cheap/m1": _conf_response(0.8)})
    estimate = OpenRouterLLMPort(transport, _escalate_router(), escalate_threshold=0.5).estimate(
        _ctx()
    )
    assert estimate.model_name == "cheap/m1"
    assert transport.calls == ["cheap/m1"]


def test_escalate_promotes_until_a_model_is_confident() -> None:
    transport = _ByModelTransport(
        {
            "cheap/m1": _conf_response(0.3),
            "mid/m2": _conf_response(0.4),
            "strong/m3": _conf_response(0.9),
        }
    )
    estimate = OpenRouterLLMPort(transport, _escalate_router(), escalate_threshold=0.5).estimate(
        _ctx()
    )
    assert estimate.model_name == "strong/m3"
    assert transport.calls == ["cheap/m1", "mid/m2", "strong/m3"]


def test_escalate_returns_the_last_model_when_none_are_confident() -> None:
    transport = _ByModelTransport(
        {
            "cheap/m1": _conf_response(0.2),
            "mid/m2": _conf_response(0.3),
            "strong/m3": _conf_response(0.4),
        }
    )
    estimate = OpenRouterLLMPort(transport, _escalate_router(), escalate_threshold=0.5).estimate(
        _ctx()
    )
    assert estimate.model_name == "strong/m3"
    assert transport.calls == ["cheap/m1", "mid/m2", "strong/m3"]


def test_make_llm_port_supports_escalate_and_ensemble_strategies() -> None:
    for strategy in ("escalate", "ensemble"):
        settings = Settings(
            _env_file=None,
            database_url="postgresql+psycopg://x/y",
            openrouter_api_key="sk-test",
            model_routing_strategy=strategy,
        )
        assert isinstance(make_llm_port(settings), OpenRouterLLMPort)


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


def test_openrouter_estimate_extracts_json_from_a_prose_preamble() -> None:
    content = (
        "Here is my reasoned forecast for this market.\n"
        + json.dumps(
            {"p_model": 0.4, "confidence": 0.5, "lower": 0.3, "upper": 0.5, "rationale": "r"}
        )
        + "\n```"
    )
    response: dict[str, Any] = {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0},
    }
    estimate = OpenRouterLLMPort(_FakeTransport([response]), ModelRouter()).estimate(_ctx())
    assert estimate.p_model == 0.4


def _critique_response(lookahead: object = False, notes: object = "ok") -> dict[str, Any]:
    content = json.dumps(
        {
            "adjusted_confidence": 0.3,
            "objections": ["weak source"],
            "lookahead_leak": lookahead,
            "notes": notes,
        }
    )
    return {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0},
    }


def _malformed_critique_response() -> dict[str, Any]:
    return {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": '{"adjusted_confidence": 0.3, "objections": [}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0},
    }


def test_critique_retries_then_falls_back_to_another_model_on_malformed_json() -> None:
    transport = _FakeTransport(
        [_malformed_critique_response(), _malformed_critique_response(), _critique_response()]
    )
    estimate = StubLLMPort().estimate(_ctx())

    verdict = OpenRouterLLMPort(transport, ModelRouter()).critique(estimate, [])

    assert verdict.objections == ["weak source"]
    assert len(transport.calls) == 3
    assert transport.calls[0]["model"] == transport.calls[1]["model"]
    assert transport.calls[2]["model"] != transport.calls[0]["model"]


def test_critique_raises_when_every_model_returns_malformed_json() -> None:
    router = ModelRouter(slates={"critic": ["a/m1", "b/m2"]})  # 2 models x 2 attempts = 4 calls
    transport = _FakeTransport([_malformed_critique_response() for _ in range(4)])
    estimate = StubLLMPort().estimate(_ctx())

    with pytest.raises(LLMResponseError):
        OpenRouterLLMPort(transport, router).critique(estimate, [])


def _malformed_response() -> dict[str, Any]:
    return {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": '{"truncated'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0},
    }


def test_estimate_falls_over_to_the_next_model_on_malformed_json() -> None:
    router = ModelRouter(slates={"estimation": ["a/first", "b/second"]})
    transport = _FakeTransport([_malformed_response(), _malformed_response(), _estimate_response()])

    estimate = OpenRouterLLMPort(transport, router).estimate(_ctx())

    assert estimate.model_name == "b/second"  # model 0 retried, then failed over to model 1
    assert [c["model"] for c in transport.calls] == ["a/first", "a/first", "b/second"]


def test_discover_falls_over_to_the_next_model_on_malformed_json() -> None:
    router = ModelRouter(slates={"discovery": ["a/first", "b/second"]})
    market = Market(id="mkt-1", external_id="x", question="q", category="c", resolution_rule="r")
    valid = {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": '{"shortlist": ["mkt-1"]}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0},
    }
    transport = _FakeTransport([_malformed_response(), _malformed_response(), valid])

    result = OpenRouterLLMPort(transport, router).discover([market], 5)

    assert result == ["mkt-1"]
    assert [c["model"] for c in transport.calls] == ["a/first", "a/first", "b/second"]


def test_critique_normalises_string_boolean_and_null_notes() -> None:
    transport = _FakeTransport([_critique_response(lookahead="false", notes=None)])
    estimate = StubLLMPort().estimate(_ctx())
    verdict = OpenRouterLLMPort(transport, ModelRouter()).critique(estimate, [])
    assert verdict.lookahead_leak is False
    assert verdict.notes is None


def test_critique_degrades_messy_field_types_to_safe_defaults() -> None:
    content = json.dumps(
        {
            "adjusted_confidence": "not a number",
            "objections": "a single objection returned as a string",
            "lookahead_leak": "The information provided is insufficient to judge a leak.",
            "notes": "ok",
        }
    )
    response: dict[str, Any] = {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0},
    }
    estimate = StubLLMPort().estimate(_ctx())
    verdict = OpenRouterLLMPort(_FakeTransport([response]), ModelRouter()).critique(estimate, [])
    assert verdict.lookahead_leak is False
    assert verdict.objections == ["a single objection returned as a string"]
    assert verdict.adjusted_confidence == estimate.confidence


def test_estimate_with_empty_slate_raises_a_clear_error() -> None:
    port = OpenRouterLLMPort(_FakeTransport([]), ModelRouter(slates={"estimation": []}))
    with pytest.raises(ValueError):
        port.estimate(_ctx())


def test_estimate_raises_typed_error_on_non_json_content() -> None:
    response: dict[str, Any] = {
        "model": "m",
        "choices": [{"message": {"content": "sorry, I cannot answer"}}],
        "usage": {},
    }
    port = OpenRouterLLMPort(_FakeTransport([response]), ModelRouter())
    with pytest.raises(LLMResponseError):
        port.estimate(_ctx())


def test_estimate_raises_typed_error_on_malformed_envelope() -> None:
    port = OpenRouterLLMPort(_FakeTransport([{"choices": []}]), ModelRouter())
    with pytest.raises(LLMResponseError):
        port.estimate(_ctx())
