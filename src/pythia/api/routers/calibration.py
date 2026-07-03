"""Calibration (§A.1): Brier(system) vs Brier(market) per category, reliability bins, EV, n."""

from typing import Literal

from fastapi import APIRouter

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import CalibrationResponse, ChannelBrier
from pythia.eval.functions import brier_score
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
    channel_records = svc.records_by_channel()
    by_channel: dict[str, ChannelBrier] = {}
    for channel, samples in channel_records.items():
        brier_sys = brier_score([p for p, _q, _o in samples], [o for _p, _q, o in samples])
        brier_mkt = brier_score([q for _p, q, _o in samples], [o for _p, _q, o in samples])
        by_channel[channel] = ChannelBrier(
            brier_system=brier_sys,
            brier_market=brier_mkt,
            n=len(samples),
            delta=brier_mkt - brier_sys,
        )
    return CalibrationResponse(
        categories=categories,
        reliability=report.reliability,
        ev=report.ev,
        cohorts=report.cohorts,
        gates=gates,
        by_channel=by_channel,
    )


@router.get("/api/calibration/recalibration")
def recalibration(session: SessionDep) -> dict[str, RecalibrationReport]:
    """Per-category raw vs recalibrated Brier on the live applied maps (not a holdout)."""
    return RecalibrationService(session).live_report()
