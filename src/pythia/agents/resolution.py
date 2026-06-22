"""Resolution agent (§5 Event Resolution): grade a subjective/UMA outcome via the routed LLM.

Grading happens after the event, so evidence is retrieved as of the resolution timestamp and
post-event reporting is in scope — unlike estimation's point-in-time boundary. The proposal is
advisory; the operator confirms the outcome, which grades the headline calibration metric.
"""

from datetime import datetime

from pythia.common import EvidencePort, LLMPort, Market, ResolutionProposal

DEFAULT_RESOLUTION_EVIDENCE_K = 8


class ResolutionAgent:
    def __init__(
        self, llm: LLMPort, evidence: EvidencePort, *, k: int = DEFAULT_RESOLUTION_EVIDENCE_K
    ) -> None:
        self._llm = llm
        self._evidence = evidence
        self._k = k

    def propose(self, market: Market, at: datetime) -> ResolutionProposal:
        evidence = self._evidence.retrieve(market.id, at, self._k)
        return self._llm.grade_resolution(market, evidence)
