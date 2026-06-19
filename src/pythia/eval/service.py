"""Calibration service (§2.1, §5.2, §4.6 inv 3): the headline Brier(system) vs Brier(market).

Wires the pure eval math to the store. Per resolved, non-ambiguous market it takes the system's
latest forecast (`p_model`) against that estimate's snapshot price (the market baseline `q`) and the
realized outcome, then reports per-category Brier with `n`, an overall reliability curve, and an
EV-predicted-vs-realized honesty check over the resolved paper trades.
"""

import uuid
from dataclasses import dataclass

from sqlmodel import Session, col, select

from pythia.common import Side, TradeStatus
from pythia.db import models
from pythia.eval.functions import (
    calibration_by_category,
    ev_predicted_vs_realized,
    reliability_bins,
)
from pythia.eval.models import CategoryCalibration, EvCalibration, ReliabilityBin

DEFAULT_RELIABILITY_BINS = 10


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
        latest = self._latest_estimates(list(outcomes))
        categories = self._categories(list(latest))
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

    def _latest_estimates(self, market_ids: list[uuid.UUID]) -> dict[uuid.UUID, models.Estimate]:
        if not market_ids:
            return {}
        rows = self._session.exec(
            select(models.Estimate)
            .where(col(models.Estimate.market_id).in_(market_ids))
            .order_by(col(models.Estimate.created_at), col(models.Estimate.id))
        ).all()
        latest: dict[uuid.UUID, models.Estimate] = {}
        for row in rows:
            latest[row.market_id] = row
        return latest

    def _categories(self, market_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not market_ids:
            return {}
        rows = self._session.exec(
            select(models.Market).where(col(models.Market.id).in_(market_ids))
        ).all()
        return {row.id: row.category for row in rows if row.id is not None}

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
