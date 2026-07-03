"""Tests for the critic agent: mandatory adversarial pass, verdict merge, model diversity."""

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pythia.agents import (
    CriticAgent,
    CriticDiversityError,
    ModelRouter,
    OpenRouterLLMPort,
    StubLLMPort,
    apply_verdict,
)
from pythia.common import CriticVerdict, Estimate, SignalSource


def _estimate(*, confidence: float = 0.8, model_name: str = "google/gemini-3-flash") -> Estimate:
    return Estimate(
        id="e1",
        market_id="m1",
        snapshot_id="s1",
        p_model=0.6,
        confidence=confidence,
        lower=0.5,
        upper=0.7,
        rationale="r",
        evidence_ids=["ev1"],
        model_name=model_name,
        signal_source=SignalSource.UNKNOWN,
    )


def _verdict(
    *,
    adjusted: float = 0.3,
    objections: list[str] | None = None,
    lookahead_leak: bool = False,
    notes: str | None = None,
) -> CriticVerdict:
    return CriticVerdict(
        adjusted_confidence=adjusted,
        objections=objections or [],
        lookahead_leak=lookahead_leak,
        notes=notes,
    )


def test_apply_verdict_lowers_confidence_to_the_critic() -> None:
    assert apply_verdict(_estimate(confidence=0.8), _verdict(adjusted=0.3)).confidence == 0.3


def test_apply_verdict_never_raises_confidence() -> None:
    assert apply_verdict(_estimate(confidence=0.4), _verdict(adjusted=0.9)).confidence == 0.4


def test_apply_verdict_zeroes_confidence_on_lookahead_leak() -> None:
    result = apply_verdict(_estimate(confidence=0.8), _verdict(adjusted=0.7, lookahead_leak=True))
    assert result.confidence == 0.0
    assert result.critic_notes is not None
    assert "leak" in result.critic_notes.lower()


_unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(_unit, _unit)
def test_lookahead_leak_always_zeroes_confidence(est_conf: float, adjusted: float) -> None:
    result = apply_verdict(
        _estimate(confidence=est_conf), _verdict(adjusted=adjusted, lookahead_leak=True)
    )
    assert result.confidence == 0.0


def test_apply_verdict_records_objections_in_notes() -> None:
    notes = apply_verdict(
        _estimate(), _verdict(objections=["weak source", "stale data"])
    ).critic_notes
    assert notes is not None
    assert "weak source" in notes
    assert "stale data" in notes


def test_apply_verdict_does_not_mutate_the_original() -> None:
    estimate = _estimate(confidence=0.8)
    result = apply_verdict(estimate, _verdict(adjusted=0.3))
    assert estimate.confidence == 0.8
    assert result is not estimate


def test_review_always_runs_and_merges_with_the_stub() -> None:
    result = CriticAgent(StubLLMPort()).review(_estimate(confidence=0.8, model_name="stub"), [])
    assert result.confidence == min(0.8, 0.1)
    assert result.critic_notes is not None


class _RecordingTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.models: list[str] = []
        self._response = response

    def complete(
        self, *, model: str, messages: list[dict[str, str]], max_price: float | None
    ) -> dict[str, Any]:
        self.models.append(model)
        return self._response


def _critique_response() -> dict[str, Any]:
    content = json.dumps(
        {"adjusted_confidence": 0.2, "objections": [], "lookahead_leak": False, "notes": "ok"}
    )
    return {
        "model": "m",
        "provider": "p",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0},
    }


def test_critic_uses_a_different_family_than_the_estimator() -> None:
    transport = _RecordingTransport(_critique_response())
    router = ModelRouter(slates={"critic": ["anthropic/claude-haiku-4.5", "openai/gpt-oss-20b"]})
    CriticAgent(OpenRouterLLMPort(transport, router)).review(
        _estimate(model_name="anthropic/claude-sonnet"), []
    )
    assert transport.models == ["openai/gpt-oss-20b"]


def test_critic_raises_when_no_diverse_model_is_available() -> None:
    transport = _RecordingTransport(_critique_response())
    router = ModelRouter(slates={"critic": ["anthropic/a", "anthropic/b"]})
    with pytest.raises(CriticDiversityError):
        CriticAgent(OpenRouterLLMPort(transport, router)).review(
            _estimate(model_name="anthropic/claude-sonnet"), []
        )
    assert transport.models == []
