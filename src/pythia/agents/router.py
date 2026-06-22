"""Config-driven model routing (§5.1): map (agent, task) to OpenRouter slugs.

The model per agent is a tuned parameter, never a hardcode. Slugs/prices here are indicative;
the harness (§5.2) picks the winner per category on resolved markets.
"""

from collections.abc import Mapping

from pythia.config.settings import RoutingStrategy

# Real, currently-available OpenRouter slugs (resolved from the live /models list, §5.1).
DEFAULT_SLATES: Mapping[str, list[str]] = {
    "discovery": ["google/gemini-2.5-flash-lite", "deepseek/deepseek-v4-flash"],
    "research": ["deepseek/deepseek-v4-flash", "google/gemini-3-flash-preview"],
    "estimation": [
        "google/gemini-3-flash-preview",
        "deepseek/deepseek-v4-flash",
        "google/gemini-2.5-flash-lite",
    ],
    "critic": ["anthropic/claude-3-haiku", "openai/gpt-oss-20b", "z-ai/glm-4.7-flash"],
    "resolution": ["deepseek/deepseek-v4-flash", "z-ai/glm-4.7-flash"],
}


class UnknownAgentError(KeyError):
    """No candidate slate is configured for the requested agent."""


class CriticDiversityError(RuntimeError):
    """No critic candidate of a different model family than the estimator is available (§5.1)."""


def model_family(model: str) -> str:
    return model.split("/", 1)[0]


_SUPPORTED_STRATEGIES = frozenset({"static", "escalate", "ensemble"})


class ModelRouter:
    def __init__(
        self,
        *,
        slates: Mapping[str, list[str]] | None = None,
        strategy: RoutingStrategy = "static",
        max_price: float | None = None,
    ) -> None:
        if strategy not in _SUPPORTED_STRATEGIES:
            raise NotImplementedError(f"routing strategy {strategy!r} is not implemented yet")
        self._slates = dict(slates) if slates is not None else dict(DEFAULT_SLATES)
        self._strategy = strategy
        self._max_price = max_price

    @property
    def strategy(self) -> RoutingStrategy:
        return self._strategy

    @property
    def max_price(self) -> float | None:
        return self._max_price

    def candidates(self, agent: str, task: str = "") -> list[str]:
        keys = [f"{agent}:{task}", agent] if task else [agent]
        for key in keys:
            slate = self._slates.get(key)
            if slate is not None:
                return list(slate)
        raise UnknownAgentError(agent)

    def diverse_candidates(self, agent: str, *, exclude_family_of: str) -> list[str]:
        excluded = model_family(exclude_family_of)
        return [model for model in self.candidates(agent) if model_family(model) != excluded]

    def ensemble_candidates(self, agent: str, size: int) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for model in self.candidates(agent):
            family = model_family(model)
            if family in seen:
                continue
            seen.add(family)
            selected.append(model)
            if len(selected) == size:
                break
        return selected

    def route(self, agent: str, task: str = "") -> str:
        return self.candidates(agent, task)[0]
