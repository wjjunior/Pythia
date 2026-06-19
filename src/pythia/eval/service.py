"""Calibration service (§2.1, §5.2, §4.6 inv 3): the headline Brier(system) vs Brier(market).

Wires the pure eval math to the store. Per resolved, non-ambiguous market it takes the system's
latest forecast (`p_model`) against that estimate's snapshot price (the market baseline `q`) and the
realized outcome, then reports per-category Brier with `n`, an overall reliability curve, and an
EV-predicted-vs-realized honesty check over the resolved paper trades.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, col, select

from pythia.common import (
    CandidateStatus,
    Estimate,
    Evidence,
    FailureCase,
    ImprovementCandidate,
    Postmortem,
    Side,
    TradeStatus,
)
from pythia.config import Settings
from pythia.config.settings import RecalibrationMethod
from pythia.db import models
from pythia.db.repositories import (
    ImprovementCandidateRepository,
    PostmortemRepository,
    RecalibrationRepository,
    ResolutionRepository,
)
from pythia.eval.functions import (
    calibration_by_category,
    ev_predicted_vs_realized,
    reliability_bins,
)
from pythia.eval.harness.models import Ablation
from pythia.eval.improvement import adoption_decision, requires_review, suggested_action
from pythia.eval.models import (
    CategoryCalibration,
    EvCalibration,
    RecalibrationReport,
    ReliabilityBin,
)
from pythia.eval.postmortem import (
    AttributionPort,
    StubAttributionAgent,
    is_category_drifting,
    is_surprising,
    surprise_score,
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

        invalid_counts = ResolutionRepository(self._session).invalid_counts_by_category()
        return CalibrationReport(
            categories=calibration_by_category(records, invalid_counts),
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


def _deterministic_id(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


class PostmortemService:
    """Impure §5.5 orchestration: trigger → attribute → persist proposals; harness-gated adoption.

    It never mutates scoring/risk and never auto-adopts. A resolution writes a post-mortem only on
    statistical surprise; each proposal becomes a `proposed` candidate that only the harness (and,
    for structural changes, the human) can move to `adopted`.
    """

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
    ) -> Postmortem | None:
        if not is_surprising(
            estimate.p_model,
            estimate.confidence,
            outcome,
            threshold=self._settings.postmortem_surprise_threshold,
            min_confidence=self._settings.postmortem_min_confidence,
        ):
            return None

        surprise = surprise_score(estimate.p_model, estimate.confidence, outcome)
        verdict = self._attribution.attribute(
            FailureCase(
                market_id=estimate.market_id,
                estimate_id=estimate.id,
                category=category,
                p_model=estimate.p_model,
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
                id=_deterministic_id("postmortem", estimate.id),
                market_id=estimate.market_id,
                estimate_id=estimate.id,
                failure_class=verdict.failure_class,
                surprise=surprise,
                rationale=verdict.rationale,
                evidence_ids=verdict.evidence_ids,
            )
        )
        action = suggested_action(verdict.failure_class)
        if action is not None:
            self._candidates.add(
                ImprovementCandidate(
                    id=_deterministic_id("candidate", postmortem.id, action.value),
                    postmortem_id=postmortem.id,
                    action=action,
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
            min_n=self._settings.improvement_min_n,
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
