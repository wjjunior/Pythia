"""Estimation agent (§5): assemble point-in-time context, delegate to the routed LLM.

The agent owns the point-in-time boundary: it retrieves evidence as of the snapshot `ts`, so the
estimate can never cite anything observed after the snapshot. Model selection lives in the
`LLMPort`/`ModelRouter`, never here. The estimate feeds the deterministic layer; it never decides.
"""

from pythia.common import (
    Estimate,
    EstimationContext,
    EvidencePort,
    LLMPort,
    Market,
    MarketSnapshot,
)

DEFAULT_EVIDENCE_K = 8


class EstimationAgent:
    def __init__(
        self, llm: LLMPort, evidence: EvidencePort, *, k: int = DEFAULT_EVIDENCE_K
    ) -> None:
        self._llm = llm
        self._evidence = evidence
        self._k = k

    def estimate(self, market: Market, snapshot: MarketSnapshot) -> Estimate:
        evidence = self._evidence.retrieve(market.id, snapshot.ts, self._k)
        context = EstimationContext(market=market, snapshot=snapshot, evidence=evidence)
        return self._llm.estimate(context)
