"""Tests for the ModelRouter (static routing, slates, max_price)."""

import pytest

from pythia.agents import ModelRouter, UnknownAgentError, model_family


def test_static_route_returns_the_first_candidate() -> None:
    router = ModelRouter()
    assert router.route("estimation") == router.candidates("estimation")[0]


def test_candidates_returns_the_agent_slate() -> None:
    candidates = ModelRouter().candidates("estimation")
    assert len(candidates) >= 1
    assert all(isinstance(slug, str) for slug in candidates)


def test_unknown_agent_raises() -> None:
    with pytest.raises(UnknownAgentError):
        ModelRouter().route("nonsense")


def test_custom_slate_overrides_the_default() -> None:
    router = ModelRouter(slates={"estimation": ["my/model"]})
    assert router.route("estimation") == "my/model"


def test_max_price_is_exposed() -> None:
    assert ModelRouter(max_price=0.5).max_price == 0.5


def test_task_specific_slate_takes_precedence() -> None:
    router = ModelRouter(slates={"estimation": ["base/model"], "estimation:fast": ["fast/model"]})
    assert router.route("estimation", "fast") == "fast/model"
    assert router.route("estimation") == "base/model"


def test_unimplemented_strategy_is_rejected() -> None:
    with pytest.raises(NotImplementedError):
        ModelRouter(strategy="ensemble")


def test_model_family_is_the_slug_prefix() -> None:
    assert model_family("google/gemini-3-flash-preview") == "google"
    assert model_family("stub") == "stub"


def test_diverse_candidates_excludes_the_estimator_family() -> None:
    router = ModelRouter(slates={"critic": ["anthropic/claude-3-haiku", "openai/gpt-oss-20b"]})
    diverse = router.diverse_candidates("critic", exclude_family_of="anthropic/claude-sonnet")
    assert diverse == ["openai/gpt-oss-20b"]


def test_diverse_candidates_keeps_all_when_families_differ() -> None:
    router = ModelRouter()
    estimator = router.route("estimation")
    assert router.diverse_candidates("critic", exclude_family_of=estimator) == router.candidates(
        "critic"
    )


def test_diverse_candidates_empty_when_all_share_the_estimator_family() -> None:
    router = ModelRouter(slates={"critic": ["anthropic/a", "anthropic/b"]})
    assert router.diverse_candidates("critic", exclude_family_of="anthropic/x") == []
