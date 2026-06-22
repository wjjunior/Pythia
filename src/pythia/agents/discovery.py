"""Discovery agent (§2.3): shortlist the open markets worth researching, via the routed LLM.

Decides which markets enter the estimation pipeline. Model selection and the deterministic stub
fallback live in the LLMPort; this agent only maps the shortlisted ids back to markets.
"""

from pythia.common import LLMPort, Market


class DiscoveryAgent:
    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    def shortlist(self, markets: list[Market], limit: int) -> list[Market]:
        ids = self._llm.discover(markets, limit)
        by_id = {market.id: market for market in markets}
        return [by_id[market_id] for market_id in ids if market_id in by_id]
