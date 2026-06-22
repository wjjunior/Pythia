"""Unit tests for the §8.5 calibration gate evaluator (pure)."""

from pythia.eval.gate import GateDecision, GateReason, GateThresholds, evaluate_gate
from pythia.eval.models import CategoryCalibration, EvCalibration

_THRESHOLDS = GateThresholds(min_n=50, brier_margin=0.01, ev_tolerance=0.05, max_invalid_rate=0.15)


def _cal(*, delta: float = 0.02, n: int = 50, invalid_rate: float = 0.05) -> CategoryCalibration:
    return CategoryCalibration(
        category="politics",
        brier_system=0.10,
        brier_market=0.10 + delta,
        delta=delta,
        n=n,
        invalid_rate=invalid_rate,
    )


def _ev(*, gap: float = 0.01) -> EvCalibration:
    return EvCalibration(mean_predicted=0.10, mean_realized=0.10 + gap, n=50)


def test_gate_clears_when_every_condition_passes() -> None:
    decision = evaluate_gate(_cal(), _ev(), _THRESHOLDS)
    assert isinstance(decision, GateDecision)
    assert decision.category == "politics"
    assert decision.cleared is True
    assert decision.reasons == ()


def test_gate_blocks_on_insufficient_n() -> None:
    decision = evaluate_gate(_cal(n=49), _ev(), _THRESHOLDS)
    assert decision.cleared is False
    assert GateReason.INSUFFICIENT_N in decision.reasons


def test_gate_blocks_when_brier_edge_is_within_the_margin() -> None:
    # delta == margin is not "beats by a margin" — strictly greater is required.
    assert GateReason.NO_BRIER_EDGE in evaluate_gate(_cal(delta=0.01), _ev(), _THRESHOLDS).reasons
    assert GateReason.NO_BRIER_EDGE in evaluate_gate(_cal(delta=-0.05), _ev(), _THRESHOLDS).reasons


def test_gate_blocks_when_ev_gap_exceeds_tolerance() -> None:
    decision = evaluate_gate(_cal(), _ev(gap=0.06), _THRESHOLDS)
    assert GateReason.EV_NOT_HONEST in decision.reasons


def test_gate_blocks_when_ev_is_missing() -> None:
    decision = evaluate_gate(_cal(), None, _THRESHOLDS)
    assert GateReason.EV_NOT_HONEST in decision.reasons


def test_gate_blocks_on_poor_resolution_quality() -> None:
    decision = evaluate_gate(_cal(invalid_rate=0.20), _ev(), _THRESHOLDS)
    assert GateReason.POOR_RESOLUTION in decision.reasons


def test_gate_collects_every_failing_reason() -> None:
    decision = evaluate_gate(_cal(delta=0.0, n=10, invalid_rate=0.5), None, _THRESHOLDS)
    assert decision.cleared is False
    assert set(decision.reasons) == {
        GateReason.INSUFFICIENT_N,
        GateReason.NO_BRIER_EDGE,
        GateReason.EV_NOT_HONEST,
        GateReason.POOR_RESOLUTION,
    }
