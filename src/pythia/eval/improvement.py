"""Improvement candidates + harness-gated adoption (§5.5). Proposes; never auto-adopts.

A failure class maps to one suggested action. Adoption is decided ONLY from a §5.2 harness ablation
on held-out resolved markets (Brier-per-dollar, with n) — never on a single bet and never without
measured improvement. Structural actions additionally require the human review gate. This module is
pure: it returns a status, it does not apply anything to scoring or risk.
"""

from pythia.common import CandidateStatus, FailureClass, ImprovementAction
from pythia.eval.harness.models import Ablation

_ACTION_BY_CLASS: dict[FailureClass, ImprovementAction | None] = {
    FailureClass.STALE_EVIDENCE: ImprovementAction.SOURCE,
    FailureClass.MISSING_EVIDENCE: ImprovementAction.SOURCE,
    FailureClass.WEAK_SOURCE: ImprovementAction.SOURCE,
    FailureClass.RESOLUTION_AMBIGUITY: ImprovementAction.GATE_CATEGORY,
    FailureClass.MODEL_OVERCONFIDENCE: ImprovementAction.RECALIBRATE,
    FailureClass.WRONG_ROUTING: ImprovementAction.REROUTE,
    FailureClass.PROMPT_GAP: ImprovementAction.PROMPT,
    FailureClass.CALIBRATED_LOSS: None,
}

# Structural changes alter pipeline behaviour and must clear the human review gate (§3.3).
_STRUCTURAL = frozenset(
    {
        ImprovementAction.REROUTE,
        ImprovementAction.PROMPT,
        ImprovementAction.GUARDRAIL,
        ImprovementAction.GATE_CATEGORY,
    }
)


def suggested_action(failure_class: FailureClass) -> ImprovementAction | None:
    return _ACTION_BY_CLASS[failure_class]


def requires_review(action: ImprovementAction) -> bool:
    return action in _STRUCTURAL


def adoption_decision(
    ablation: Ablation,
    *,
    min_n: int,
    requires_human_review: bool,
    human_approved: bool,
) -> CandidateStatus:
    if ablation.variant.n < min_n:
        return CandidateStatus.VALIDATING  # not enough held-out evidence yet
    improved = ablation.brier_per_dollar_delta > 0.0 and ablation.brier_delta_change > 0.0
    if not improved:
        return CandidateStatus.REJECTED
    if requires_human_review and not human_approved:
        return CandidateStatus.VALIDATING  # measured improvement, awaiting the human gate
    return CandidateStatus.ADOPTED
