"""Tests for improvement candidates + harness-gated adoption (§5.5): the never-auto-adopt guard."""

from pathlib import Path

import pythia.eval.improvement as improvement_module
import pythia.eval.postmortem as postmortem_module
import pythia.eval.service as service_module
from pythia.common import CandidateStatus, FailureClass, ImprovementAction
from pythia.eval.harness.models import Ablation, ConfigReport
from pythia.eval.improvement import adoption_decision, requires_review, suggested_action

_MIN_N = 30


def _report(*, n: int, brier_per_dollar: float, brier_delta: float) -> ConfigReport:
    return ConfigReport(
        name="cfg",
        brier_system=0.2,
        brier_market=0.25,
        brier_delta=brier_delta,
        brier_per_dollar=brier_per_dollar,
        mean_latency_ms=0.0,
        error_rate=0.0,
        total_cost_usd=1.0,
        n=n,
        by_category=[],
    )


def _ablation(*, n: int, bpd_delta: float, brier_delta_change: float) -> Ablation:
    base = _report(n=n, brier_per_dollar=0.0, brier_delta=0.0)
    variant = _report(n=n, brier_per_dollar=bpd_delta, brier_delta=brier_delta_change)
    return Ablation(
        base=base,
        variant=variant,
        brier_per_dollar_delta=bpd_delta,
        brier_delta_change=brier_delta_change,
    )


def test_action_mapping_and_calibrated_loss_has_no_action() -> None:
    assert suggested_action(FailureClass.WRONG_ROUTING) == ImprovementAction.REROUTE
    assert suggested_action(FailureClass.MODEL_OVERCONFIDENCE) == ImprovementAction.RECALIBRATE
    assert suggested_action(FailureClass.MISSING_EVIDENCE) == ImprovementAction.SOURCE
    assert suggested_action(FailureClass.CALIBRATED_LOSS) is None


def test_structural_actions_require_human_review() -> None:
    assert requires_review(ImprovementAction.REROUTE)
    assert requires_review(ImprovementAction.GATE_CATEGORY)
    assert not requires_review(ImprovementAction.RECALIBRATE)
    assert not requires_review(ImprovementAction.SOURCE)


def test_never_adopted_without_enough_held_out_evidence() -> None:
    ablation = _ablation(n=_MIN_N - 1, bpd_delta=1.0, brier_delta_change=0.1)
    status = adoption_decision(
        ablation, min_n=_MIN_N, requires_human_review=False, human_approved=False
    )
    assert status == CandidateStatus.VALIDATING


def test_rejected_when_the_harness_shows_no_improvement() -> None:
    ablation = _ablation(n=_MIN_N, bpd_delta=-0.5, brier_delta_change=-0.1)
    status = adoption_decision(
        ablation, min_n=_MIN_N, requires_human_review=False, human_approved=False
    )
    assert status == CandidateStatus.REJECTED


def test_non_structural_adopts_on_measured_improvement() -> None:
    ablation = _ablation(n=_MIN_N, bpd_delta=0.5, brier_delta_change=0.05)
    status = adoption_decision(
        ablation, min_n=_MIN_N, requires_human_review=False, human_approved=False
    )
    assert status == CandidateStatus.ADOPTED


def test_structural_improvement_awaits_the_human_gate() -> None:
    ablation = _ablation(n=_MIN_N, bpd_delta=0.5, brier_delta_change=0.05)
    pending = adoption_decision(
        ablation, min_n=_MIN_N, requires_human_review=True, human_approved=False
    )
    assert pending == CandidateStatus.VALIDATING
    approved = adoption_decision(
        ablation, min_n=_MIN_N, requires_human_review=True, human_approved=True
    )
    assert approved == CandidateStatus.ADOPTED


def test_loop_never_touches_the_money_path() -> None:
    # Structural guarantee: the loop — including its impure orchestrator — cannot mutate
    # scoring/risk. It only proposes.
    for module in (postmortem_module, improvement_module, service_module):
        assert module.__file__ is not None
        source = Path(module.__file__).read_text()
        assert "pythia.scoring" not in source
        assert "pythia.risk" not in source
