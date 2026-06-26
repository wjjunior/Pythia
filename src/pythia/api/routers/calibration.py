"""Calibration (§A.1): Brier(system) vs Brier(market) per category, reliability bins, EV, n."""

from typing import Literal

from fastapi import APIRouter

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import CalibrationResponse
from pythia.eval.models import RecalibrationReport
from pythia.services.calibration import (
    CalibrationService,
    RecalibrationService,
    gate_thresholds_from_settings,
)

router = APIRouter(tags=["calibration"])


@router.get("/api/calibration")
def calibration(
    session: SessionDep,
    settings: SettingsDep,
    category: str | None = None,
    baseline: Literal["market", "blended"] = "market",
) -> CalibrationResponse:
    svc = CalibrationService(session, settings=settings)
    report = svc.blended_report() if baseline == "blended" else svc.report()
    gates = svc.gate_status(gate_thresholds_from_settings(settings))
    categories = report.categories
    if category is not None:
        categories = [item for item in categories if item.category == category]
        gates = [gate for gate in gates if gate.category == category]
    return CalibrationResponse(
        categories=categories,
        reliability=report.reliability,
        ev=report.ev,
        cohorts=report.cohorts,
        gates=gates,
    )


@router.get("/api/calibration/recalibration")
def recalibration(session: SessionDep) -> dict[str, RecalibrationReport]:
    """Per-category raw vs recalibrated Brier on the live applied maps (not a holdout)."""
    return RecalibrationService(session).live_report()
