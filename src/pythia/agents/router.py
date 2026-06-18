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


class ModelRouter:
    def __init__(
        self,
        *,
        slates: Mapping[str, list[str]] | None = None,
        strategy: RoutingStrategy = "static",
        max_price: float | None = None,
    ) -> None:
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
        try:
            return list(self._slates[agent])
        except KeyError as exc:
            raise UnknownAgentError(agent) from exc

    def route(self, agent: str, task: str = "") -> str:
        return self.candidates(agent, task)[0]
