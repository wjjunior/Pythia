"""Tests for the ModelRouter (static routing, slates, max_price)."""

import pytest

from pythia.agents import ModelRouter, UnknownAgentError


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
