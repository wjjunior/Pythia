"""Tests for the discovery agent and the LLMPort.discover shortlisting capability."""

import json
from typing import Any

from pythia.agents import DiscoveryAgent, ModelRouter, OpenRouterLLMPort, StubLLMPort
from pythia.common import CostEvent, LLMPort, Market


def _market(mid: str) -> Market:
    return Market(id=mid, external_id=f"ext-{mid}", question="q", category="c", resolution_rule="r")


def _markets() -> list[Market]:
    return [_market("m3"), _market("m1"), _market("m2")]


class _FakeTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses)

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "messages": messages, "max_price": max_price})
        return self._responses.pop(0)


def _discovery_response(ids: list[str], *, cost: float = 0.0003) -> dict[str, Any]:
    return {
        "model": "disc/model",
        "provider": "prov",
        "choices": [{"message": {"content": json.dumps({"shortlist": ids})}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": cost},
    }


def _openrouter(transport: _FakeTransport, cost_sink: Any = None) -> OpenRouterLLMPort:
    router = ModelRouter(slates={"discovery": ["disc/model"]}, max_price=0.5)
    return OpenRouterLLMPort(transport, router, cost_sink=cost_sink)


def test_stub_discover_should_return_a_deterministic_capped_shortlist() -> None:
    port: LLMPort = StubLLMPort()
    result = port.discover(_markets(), 2)
    assert result == ["m1", "m2"]
    assert StubLLMPort().discover(_markets(), 2) == result


def test_openrouter_discover_should_parse_shortlist_and_record_cost() -> None:
    costs: list[CostEvent] = []
    port = _openrouter(_FakeTransport([_discovery_response(["m2", "m1"])]), cost_sink=costs.append)
    result = port.discover(_markets(), 5)
    assert result == ["m2", "m1"]
    assert len(costs) == 1
    assert costs[0].agent == "discovery"
    assert costs[0].task == "discover"
    assert costs[0].cost_usd == 0.0003


def test_openrouter_discover_should_drop_ids_not_in_the_input() -> None:
    port = _openrouter(_FakeTransport([_discovery_response(["m2", "ghost", "m1"])]))
    assert port.discover(_markets(), 5) == ["m2", "m1"]


def test_openrouter_discover_should_cap_at_the_limit() -> None:
    port = _openrouter(_FakeTransport([_discovery_response(["m3", "m2", "m1"])]))
    assert port.discover(_markets(), 2) == ["m3", "m2"]


def test_discovery_agent_should_map_ids_to_markets_preserving_order() -> None:
    agent = DiscoveryAgent(_openrouter(_FakeTransport([_discovery_response(["m2", "m1"])])))
    shortlist = agent.shortlist(_markets(), 5)
    assert [market.id for market in shortlist] == ["m2", "m1"]
