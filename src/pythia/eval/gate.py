"""Calibration gate evaluator (§8.5): the objective per-category go/no-go for real-money execution.

Pure: a category clears only on accumulated, measured calibration — it must beat the market Brier by
a margin over a significant number of resolutions, with EV honest (realized ≈ predicted) and
resolution quality acceptable. This decision is the prerequisite to real-money execution (PYT-58);
the module deliberately never wires it. Thresholds enter as a parameter, not from config.

"Stable, not lucky" is approximated by a large min_n plus a positive Brier margin; a statistical
significance test (a confidence interval on the Brier delta) and per-category EV are future
refinements that need the per-market paired briers exposed.
"""

from dataclasses import dataclass
from enum import StrEnum

from pythia.eval.models import CategoryCalibration, EvCalibration


class GateReason(StrEnum):
    INSUFFICIENT_N = "insufficient_n"
    NO_BRIER_EDGE = "no_brier_edge"
    EV_NOT_HONEST = "ev_not_honest"
    POOR_RESOLUTION = "poor_resolution"


@dataclass(frozen=True, slots=True)
class GateThresholds:
    min_n: int
    brier_margin: float
    ev_tolerance: float
    max_invalid_rate: float


@dataclass(frozen=True, slots=True)
class GateDecision:
    category: str
    cleared: bool
    reasons: tuple[GateReason, ...]


def evaluate_gate(
    calibration: CategoryCalibration,
    ev: EvCalibration | None,
    thresholds: GateThresholds,
) -> GateDecision:
    reasons: list[GateReason] = []
    if calibration.n < thresholds.min_n:
        reasons.append(GateReason.INSUFFICIENT_N)
    if calibration.delta <= thresholds.brier_margin:
        reasons.append(GateReason.NO_BRIER_EDGE)
    if ev is None or abs(ev.mean_predicted - ev.mean_realized) > thresholds.ev_tolerance:
        reasons.append(GateReason.EV_NOT_HONEST)
    if calibration.invalid_rate > thresholds.max_invalid_rate:
        reasons.append(GateReason.POOR_RESOLUTION)
    return GateDecision(category=calibration.category, cleared=not reasons, reasons=tuple(reasons))
