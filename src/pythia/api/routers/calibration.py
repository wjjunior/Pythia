"""Calibration (§A.1): Brier(system) vs Brier(market) per category, reliability bins, EV, n."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep
from pythia.api.schemas import CalibrationResponse
from pythia.eval.service import CalibrationService

router = APIRouter(tags=["calibration"])


@router.get("/api/calibration")
def calibration(session: SessionDep, category: str | None = None) -> CalibrationResponse:
    report = CalibrationService(session).report()
    categories = report.categories
    if category is not None:
        categories = [item for item in categories if item.category == category]
    return CalibrationResponse(categories=categories, reliability=report.reliability, ev=report.ev)
