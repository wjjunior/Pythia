"""Calibration service (§2.1, §5.2, §4.6 inv 3): the headline Brier(system) vs Brier(market).

Wires the pure eval math to the store. Per resolved, non-ambiguous market it takes the system's
latest forecast (`p_model`) against that estimate's snapshot price (the market baseline `q`) and the
realized outcome, then reports per-category Brier with `n`, an overall reliability curve, and an
EV-predicted-vs-realized honesty check over the resolved paper trades.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, col, select

from pythia.common import Side, TradeStatus
from pythia.config.settings import RecalibrationMethod
from pythia.db import models
from pythia.db.repositories import RecalibrationRepository
from pythia.eval.functions import (
    calibration_by_category,
    ev_predicted_vs_realized,
    reliability_bins,
)
from pythia.eval.models import (
    CategoryCalibration,
    EvCalibration,
    RecalibrationReport,
    ReliabilityBin,
)
from pythia.eval.recalibration import Sample, compare_brier, recalibrate

DEFAULT_RELIABILITY_BINS = 10


def _latest_estimate_per_market(
    session: Session, market_ids: list[uuid.UUID]
) -> dict[uuid.UUID, models.Estimate]:
    if not market_ids:
        return {}
    rows = session.exec(
        select(models.Estimate)
        .where(col(models.Estimate.market_id).in_(market_ids))
        .order_by(col(models.Estimate.created_at), col(models.Estimate.id))
    ).all()
    latest: dict[uuid.UUID, models.Estimate] = {}
    for row in rows:
        latest[row.market_id] = row
    return latest


def _categories_of(session: Session, market_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not market_ids:
        return {}
    rows = session.exec(select(models.Market).where(col(models.Market.id).in_(market_ids))).all()
    return {row.id: row.category for row in rows if row.id is not None}


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    categories: list[CategoryCalibration]
    reliability: list[ReliabilityBin]
    ev: EvCalibration | None


class CalibrationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def report(self, *, n_bins: int = DEFAULT_RELIABILITY_BINS) -> CalibrationReport:
        outcomes = self._resolved_outcomes()
        records = self._records(outcomes)
        forecasts = [p_model for samples in records.values() for (p_model, _q, _o) in samples]
        observed = [outcome for samples in records.values() for (_p, _q, outcome) in samples]

        return CalibrationReport(
            categories=calibration_by_category(records),
            reliability=reliability_bins(forecasts, observed, n_bins) if forecasts else [],
            ev=self._ev(outcomes),
        )

    def records(self) -> dict[str, list[tuple[float, float, int]]]:
        return self._records(self._resolved_outcomes())

    def _records(self, outcomes: dict[uuid.UUID, int]) -> dict[str, list[tuple[float, float, int]]]:
        latest = _latest_estimate_per_market(self._session, list(outcomes))
        categories = _categories_of(self._session, list(latest))
        prices = self._snapshot_prices([estimate.snapshot_id for estimate in latest.values()])
        records: dict[str, list[tuple[float, float, int]]] = {}
        for market_id, estimate in latest.items():
            record = (estimate.p_model, prices[estimate.snapshot_id], outcomes[market_id])
            records.setdefault(categories[market_id], []).append(record)
        return records

    def _resolved_outcomes(self) -> dict[uuid.UUID, int]:
        rows = self._session.exec(
            select(models.Resolution).where(col(models.Resolution.ambiguous).is_(False))
        ).all()
        return {row.market_id: row.outcome for row in rows}

    def _snapshot_prices(self, snapshot_ids: list[uuid.UUID]) -> dict[uuid.UUID, float]:
        if not snapshot_ids:
            return {}
        rows = self._session.exec(
            select(models.MarketSnapshot).where(col(models.MarketSnapshot.id).in_(snapshot_ids))
        ).all()
        return {row.id: row.yes_price for row in rows if row.id is not None}

    def _ev(self, outcomes: dict[uuid.UUID, int]) -> EvCalibration | None:
        if not outcomes:
            return None
        trades = self._session.exec(
            select(models.PaperTrade)
            .where(col(models.PaperTrade.market_id).in_(list(outcomes)))
            .where(models.PaperTrade.status == TradeStatus.RESOLVED)
        ).all()
        if not trades:
            return None
        p_models = {
            row.id: row.p_model
            for row in self._session.exec(
                select(models.Estimate).where(
                    col(models.Estimate.id).in_([trade.estimate_id for trade in trades])
                )
            ).all()
        }
        predicted: list[float] = []
        realized: list[float] = []
        for trade in trades:
            side = Side(trade.side)
            outcome = outcomes[trade.market_id]
            p_model = p_models[trade.estimate_id]
            p_win = p_model if side == Side.YES else 1.0 - p_model
            won = (side == Side.YES and outcome == 1) or (side == Side.NO and outcome == 0)
            predicted.append(p_win - trade.entry_price)
            realized.append((1.0 if won else 0.0) - trade.entry_price)
        return ev_predicted_vs_realized(predicted, realized)


class RecalibrationService:
    """Fits and versions per-category p_model→p_calibrated maps from the resolved track record."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def fit_and_store(
        self, asof: datetime, *, method: RecalibrationMethod, min_n: int
    ) -> dict[str, str]:
        repository = RecalibrationRepository(self._session)
        stored: dict[str, str] = {}
        for category, samples in self._samples_by_category().items():
            cmap = recalibrate(samples, asof, method=method, min_n=min_n)
            n_fit = sum(1 for _p, _o, resolved_at in samples if resolved_at < asof)
            repository.save(category, cmap, n_fit)
            stored[category] = cmap.method
        return stored

    def evaluate(
        self, asof: datetime, *, method: RecalibrationMethod, min_n: int
    ) -> dict[str, RecalibrationReport]:
        reports: dict[str, RecalibrationReport] = {}
        for category, samples in self._samples_by_category().items():
            eligible = sorted(
                (sample for sample in samples if sample[2] < asof), key=lambda sample: sample[2]
            )
            if len(eligible) < 2:
                continue
            split = len(eligible) // 2
            train, test = eligible[:split], eligible[split:]
            cmap = recalibrate(train, test[0][2], method=method, min_n=min_n)
            reports[category] = compare_brier([(p, o) for p, o, _ in test], cmap)
        return reports

    def _samples_by_category(self) -> dict[str, list[Sample]]:
        resolutions = self._resolutions()
        latest = _latest_estimate_per_market(self._session, list(resolutions))
        categories = _categories_of(self._session, list(latest))
        by_category: dict[str, list[Sample]] = {}
        for market_id, estimate in latest.items():
            outcome, resolved_at = resolutions[market_id]
            by_category.setdefault(categories[market_id], []).append(
                (estimate.p_model, outcome, resolved_at)
            )
        return by_category

    def _resolutions(self) -> dict[uuid.UUID, tuple[int, datetime]]:
        rows = self._session.exec(
            select(models.Resolution).where(col(models.Resolution.ambiguous).is_(False))
        ).all()
        return {row.market_id: (row.outcome, row.resolved_at) for row in rows}
