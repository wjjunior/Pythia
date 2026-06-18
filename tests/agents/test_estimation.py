"""Tests for the estimation agent: point-in-time context assembly + routed estimation."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pythia.agents import EstimationAgent, ModelRouter, OpenRouterLLMPort, StubLLMPort
from pythia.common import Estimate, Evidence, Market, MarketSnapshot

_TS = datetime(2026, 3, 1, tzinfo=UTC)


def _market() -> Market:
    return Market(
        id="m1",
        external_id="x",
        question="Will it rain tomorrow?",
        category="weather",
        resolution_rule="rule",
    )


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        id="s1", market_id="m1", ts=_TS, yes_price=0.6, spread=0.02, volume=1.0, liquidity=1.0
    )


def _evidence(eid: str, observed_at: datetime) -> Evidence:
    return Evidence(
        id=eid, market_id="m1", source_id="src1", observed_at=observed_at, content=f"signal {eid}"
    )


_PAST = [_evidence("e1", _TS - timedelta(days=2)), _evidence("e2", _TS - timedelta(hours=1))]
_FUTURE = [_evidence("e3", _TS + timedelta(days=1))]


class _FakeEvidencePort:
    def __init__(self, evidence: list[Evidence]) -> None:
        self.calls: list[tuple[str, datetime, int]] = []
        self._evidence = evidence

    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]:
        self.calls.append((market_id, asof, k))
        return [item for item in self._evidence if item.observed_at <= asof][:k]


class _FakeTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        return self._responses.pop(0)


def _estimate_response(model: str) -> dict[str, Any]:
    content = json.dumps(
        {"p_model": 0.62, "confidence": 0.55, "lower": 0.5, "upper": 0.72, "rationale": "because"}
    )
    return {
        "model": model,
        "provider": "p",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0},
    }


def test_estimate_produces_a_valid_cited_estimate_with_the_stub() -> None:
    port = _FakeEvidencePort(_PAST + _FUTURE)
    estimate = EstimationAgent(StubLLMPort(), port, k=8).estimate(_market(), _snapshot())
    assert isinstance(estimate, Estimate)
    assert 0.0 <= estimate.p_model <= 1.0
    assert estimate.lower <= estimate.upper
    assert estimate.evidence_ids == ["e1", "e2"]
    assert estimate.model_name == "stub"


def test_estimate_retrieves_evidence_as_of_the_snapshot_ts() -> None:
    port = _FakeEvidencePort(_PAST + _FUTURE)
    EstimationAgent(StubLLMPort(), port, k=5).estimate(_market(), _snapshot())
    assert port.calls == [("m1", _TS, 5)]


def test_estimate_never_cites_evidence_newer_than_the_snapshot() -> None:
    port = _FakeEvidencePort(_PAST + _FUTURE)
    estimate = EstimationAgent(StubLLMPort(), port).estimate(_market(), _snapshot())
    assert "e3" not in estimate.evidence_ids


def test_model_is_selected_by_the_router_not_hardcoded() -> None:
    port = _FakeEvidencePort(_PAST)
    transport = _FakeTransport([_estimate_response("custom/model-x")])
    llm = OpenRouterLLMPort(transport, ModelRouter(slates={"estimation": ["custom/model-x"]}))
    estimate = EstimationAgent(llm, port).estimate(_market(), _snapshot())
    assert estimate.model_name == "custom/model-x"
