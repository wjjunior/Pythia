"""Critic agent (§5.1): the mandatory adversarial pass between estimation and scoring.

The critic always runs, on a different model family than the estimator (enforced by the routing
layer). Its verdict is merged deterministically: confidence is lowered to the critic's number,
never averaged, and a detected lookahead leak voids the estimate by zeroing confidence — a
point-in-time violation must not bet.
"""

from pythia.common import CriticVerdict, Estimate, Evidence, LLMPort

LOOKAHEAD_LEAK_CONFIDENCE = 0.0


def _critic_notes(verdict: CriticVerdict) -> str | None:
    parts: list[str] = []
    if verdict.lookahead_leak:
        parts.append("lookahead leak detected")
    parts.extend(verdict.objections)
    if verdict.notes:
        parts.append(verdict.notes)
    return " | ".join(parts) if parts else None


def apply_verdict(estimate: Estimate, verdict: CriticVerdict) -> Estimate:
    confidence = min(estimate.confidence, verdict.adjusted_confidence)
    if verdict.lookahead_leak:
        confidence = LOOKAHEAD_LEAK_CONFIDENCE
    return estimate.model_copy(
        update={"confidence": confidence, "critic_notes": _critic_notes(verdict)}
    )


class CriticAgent:
    def __init__(self, llm: LLMPort) -> None:
        self._llm = llm

    def review(self, estimate: Estimate, evidence: list[Evidence]) -> Estimate:
        verdict = self._llm.critique(estimate, evidence)
        return apply_verdict(estimate, verdict)
