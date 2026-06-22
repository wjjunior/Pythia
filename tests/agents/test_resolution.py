"""Tests for the resolution agent and the LLMPort.grade_resolution capability."""

import json
from datetime import UTC, datetime
from typing import Any

from pythia.agents import (
    DEFAULT_RESOLUTION_EVIDENCE_K,
    ModelRouter,
    OpenRouterLLMPort,
    ResolutionAgent,
    StubLLMPort,
)
from pythia.common import CostEvent, Evidence, Market, ResolutionProposal

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _market() -> Market:
    return Market(
        id="m1",
        external_id="x",
        question="Did the senate pass the bill?",
        category="politics",
        resolution_rule="UMA optimistic oracle",
        resolution_source="subjective_uma",
    )


def _evidence() -> list[Evidence]:
    return [
        Evidence(id="e1", market_id="m1", source_id="src1", observed_at=_TS, content="passed 60-40")
    ]


class _FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "messages": messages})
        return self._response


def _resolution_response(payload: dict[str, Any], *, cost: float = 0.0004) -> dict[str, Any]:
    return {
        "model": "res/model",
        "provider": "deepseek",
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 800, "completion_tokens": 60, "cost": cost},
    }


def _openrouter(transport: _FakeTransport, costs: list[CostEvent]) -> OpenRouterLLMPort:
    router = ModelRouter(slates={"resolution": ["res/model"]}, max_price=0.5)
    return OpenRouterLLMPort(transport, router, cost_sink=costs.append)


def test_stub_grade_resolution_should_return_an_ambiguous_proposal() -> None:
    proposal = StubLLMPort().grade_resolution(_market(), _evidence())
    assert isinstance(proposal, ResolutionProposal)
    assert proposal.ambiguous is True
    assert proposal.outcome is None
    assert proposal.confidence == 0.0
    assert proposal.evidence_ids == ["e1"]
    assert proposal.model_name == "stub"


def test_openrouter_grade_resolution_should_map_a_concrete_grade_and_emit_cost() -> None:
    costs: list[CostEvent] = []
    transport = _FakeTransport(
        _resolution_response(
            {"outcome": 1, "ambiguous": False, "confidence": 0.8, "rationale": "passed 60-40"}
        )
    )
    proposal = _openrouter(transport, costs).grade_resolution(_market(), _evidence())
    assert proposal.outcome == 1
    assert proposal.ambiguous is False
    assert proposal.confidence == 0.8
    assert proposal.model_name == "res/model"
    assert proposal.evidence_ids == ["e1"]
    assert len(costs) == 1
    assert costs[0].agent == "resolution"
    assert costs[0].cost_usd == 0.0004


def test_openrouter_grade_resolution_should_flag_ambiguous_when_the_model_grades_invalid() -> None:
    transport = _FakeTransport(
        _resolution_response(
            {"outcome": None, "ambiguous": True, "confidence": 0.2, "rationale": "disputed"}
        )
    )
    proposal = _openrouter(transport, []).grade_resolution(_market(), _evidence())
    assert proposal.ambiguous is True
    assert proposal.outcome is None


class _RecordingEvidence:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.calls: list[tuple[str, datetime, int]] = []
        self._evidence = evidence

    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]:
        self.calls.append((market_id, asof, k))
        return self._evidence


def test_resolution_agent_should_grade_evidence_retrieved_as_of_the_resolution_time() -> None:
    store = _RecordingEvidence(_evidence())
    agent = ResolutionAgent(StubLLMPort(), store)
    at = datetime(2026, 6, 1, tzinfo=UTC)
    proposal = agent.propose(_market(), at)
    assert store.calls == [("m1", at, DEFAULT_RESOLUTION_EVIDENCE_K)]
    assert proposal.evidence_ids == ["e1"]
    assert proposal.ambiguous is True
