"""Post-mortem service (§5.5): impure trigger → attribute → persist proposals; harness-gated.

Lives outside the pure `eval/` package. It never mutates scoring/risk and never auto-adopts. A
resolution writes a post-mortem only on statistical surprise; each proposal becomes a `proposed`
candidate that only the harness (and, for structural changes, the human) can move to `adopted`.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlmodel import Session

from pythia.common import (
    CandidateStatus,
    Estimate,
    Evidence,
    FailureCase,
    ImprovementCandidate,
    Postmortem,
)
from pythia.common.ids import deterministic_id
from pythia.config import Settings
from pythia.db.repositories import ImprovementCandidateRepository, PostmortemRepository
from pythia.eval.functions import brier
from pythia.eval.harness.models import Ablation
from pythia.eval.improvement import adoption_decision, requires_review, suggested_action
from pythia.eval.postmortem import (
    AttributionPort,
    StubAttributionAgent,
    is_category_drifting,
    is_surprising,
    surprise_score,
)


class PostmortemService:
    def __init__(
        self, session: Session, settings: Settings, *, attribution: AttributionPort | None = None
    ) -> None:
        self._settings = settings
        self._postmortems = PostmortemRepository(session)
        self._candidates = ImprovementCandidateRepository(session)
        self._attribution = attribution or StubAttributionAgent(
            stale_evidence_days=settings.postmortem_stale_evidence_days
        )

    def review_resolution(
        self,
        estimate: Estimate,
        evidence: list[Evidence],
        *,
        category: str,
        outcome: int,
        ambiguous: bool,
        asof: datetime,
        resolution_id: str | None = None,
    ) -> Postmortem | None:
        # Surprise and the stored Brier measure the committed (recalibrated) forecast the system
        # acted on, not the raw estimator output (PYT-112); None ⇒ raw is the committed value.
        committed = (
            estimate.p_model_calibrated
            if estimate.p_model_calibrated is not None
            else estimate.p_model
        )
        if not is_surprising(
            committed,
            estimate.confidence,
            outcome,
            threshold=self._settings.postmortem_surprise_threshold,
            min_confidence=self._settings.postmortem_min_confidence,
        ):
            return None

        surprise = surprise_score(committed, estimate.confidence, outcome)
        verdict = self._attribution.attribute(
            FailureCase(
                market_id=estimate.market_id,
                estimate_id=estimate.id,
                category=category,
                p_model=committed,
                confidence=estimate.confidence,
                outcome=outcome,
                surprise=surprise,
                asof=asof,
                resolution_ambiguous=ambiguous,
                evidence=evidence,
            )
        )
        postmortem = self._postmortems.add(
            Postmortem(
                id=deterministic_id("postmortem", estimate.id),
                market_id=estimate.market_id,
                category=category,
                estimate_id=estimate.id,
                resolution_id=resolution_id,
                failure_class=verdict.failure_class,
                brier=brier(committed, outcome),
                rationale=verdict.rationale,
                evidence_ids=verdict.evidence_ids,
            )
        )
        action = suggested_action(verdict.failure_class)
        if action is not None:
            self._candidates.add(
                ImprovementCandidate(
                    id=deterministic_id("candidate", postmortem.id, action.value),
                    postmortem_id=postmortem.id,
                    kind=action,
                    status=CandidateStatus.PROPOSED,
                    rationale=verdict.rationale,
                    requires_review=requires_review(action),
                )
            )
        return postmortem

    def detect_category_drift(self, brier_deltas: Sequence[float]) -> bool:
        """The aggregate-drift trigger: is a category's Brier-vs-market advantage eroding?

        `brier_deltas` is the category's chronological per-resolution `brier_market - brier_system`.
        Like the surprise trigger, this reacts to a trend, never a single outcome.
        """
        return is_category_drifting(brier_deltas, window=self._settings.postmortem_drift_window)

    def validate_candidate(
        self, candidate_id: str, ablation: Ablation, *, human_approved: bool = False
    ) -> ImprovementCandidate:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise KeyError(candidate_id)
        status = adoption_decision(
            ablation,
            min_n=self._settings.improvement_validation_min_n,
            requires_human_review=candidate.requires_review,
            human_approved=human_approved,
        )
        return self._candidates.set_status(
            candidate_id,
            status,
            brier_per_dollar=ablation.variant.brier_per_dollar,
            n=ablation.variant.n,
            human_approved=human_approved if candidate.requires_review else None,
        )
