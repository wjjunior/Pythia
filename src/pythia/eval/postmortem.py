"""Post-mortem trigger + failure attribution (§5.5): the honest post-resolution loop.

A resolution is examined only for statistical SURPRISE (a confident forecast the outcome
contradicted) or category Brier DRIFT against the market — never a lone calibrated loss, which a
calibrated model must incur ~30% of the time on its 70% bets. The attribution agent runs off the
money path, classifies the failure into a fixed taxonomy, and only proposes: it never mutates
scoring/risk and never adopts.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from pythia.common import Evidence, FailureCase, FailureClass, PostmortemVerdict
from pythia.eval.functions import brier

OVERCONFIDENCE_CONFIDENCE = 0.7


def surprise_score(p_model: float, confidence: float, outcome: int) -> float:
    return confidence * brier(p_model, outcome)


def is_surprising(
    p_model: float,
    confidence: float,
    outcome: int,
    *,
    threshold: float,
    min_confidence: float,
) -> bool:
    if confidence < min_confidence:
        return False
    return surprise_score(p_model, confidence, outcome) >= threshold


def is_category_drifting(brier_deltas: Sequence[float], *, window: int) -> bool:
    # Each delta = brier_market - brier_system (positive ⇒ system better calibrated).
    # Drift = the recent window lost ground vs the preceding window AND no longer beats the market.
    if window <= 0 or len(brier_deltas) < 2 * window:
        return False
    recent = sum(brier_deltas[-window:]) / window
    prior = sum(brier_deltas[-2 * window : -window]) / window
    return recent < prior and recent <= 0.0


class AttributionPort(Protocol):
    def attribute(self, case: FailureCase) -> PostmortemVerdict: ...


def _is_stale(evidence: list[Evidence], asof: datetime, *, max_age_days: int) -> bool:
    if not evidence:
        return False
    newest = max(item.observed_at for item in evidence)
    return (asof - newest) > timedelta(days=max_age_days)


class StubAttributionAgent:
    """Deterministic, zero-secret attribution. Classifies from explicit signals; an LLM adapter can
    drop in via AttributionPort for the nuanced classes (weak source, prompt gap, wrong routing)."""

    def __init__(self, *, stale_evidence_days: int = 30) -> None:
        self._stale_days = stale_evidence_days

    def attribute(self, case: FailureCase) -> PostmortemVerdict:
        cited = [item.id for item in case.evidence]
        if case.resolution_ambiguous:
            return PostmortemVerdict(
                failure_class=FailureClass.RESOLUTION_AMBIGUITY,
                rationale="resolution flagged ambiguous/disputed; not a model error",
                evidence_ids=cited,
            )
        if not case.evidence:
            return PostmortemVerdict(
                failure_class=FailureClass.MISSING_EVIDENCE,
                rationale="no evidence was cited for a surprising, confident miss",
            )
        if _is_stale(case.evidence, case.asof, max_age_days=self._stale_days):
            return PostmortemVerdict(
                failure_class=FailureClass.STALE_EVIDENCE,
                rationale="newest cited evidence predates the staleness window",
                evidence_ids=cited,
            )
        if case.confidence >= OVERCONFIDENCE_CONFIDENCE:
            return PostmortemVerdict(
                failure_class=FailureClass.MODEL_OVERCONFIDENCE,
                rationale="high confidence on an outcome the evidence did not support",
                evidence_ids=cited,
            )
        return PostmortemVerdict(
            failure_class=FailureClass.CALIBRATED_LOSS,
            rationale="no attributable defect found; within calibrated variance",
            evidence_ids=cited,
        )
