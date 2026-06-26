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
    RecalibrationMethod,
    Side,
    TradeStatus,
)
from pythia.common.ids import deterministic_id
from pythia.common.venue import blended_baseline, latest_prices_per_venue
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    ImprovementCandidateRepository,
    PostmortemRepository,
    RecalibrationRepository,
    ResolutionRepository,
    VenueQuoteRepository,
)
from pythia.eval.cohorts import Cohort, cohort_policy, cohorts_for
from pythia.eval.functions import (
    brier,
    calibration_by_category,
    calibration_by_cohort,
    ev_predicted_vs_realized,
    reliability_bins,
)
from pythia.eval.gate import GateDecision, GateThresholds, evaluate_gate
from pythia.eval.harness.models import Ablation
from pythia.eval.improvement import adoption_decision, requires_review, suggested_action
from pythia.eval.models import (
    CategoryCalibration,
    CohortCalibration,
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

_Records = dict[str, list[tuple[float, float, int]]]


def _latest_estimate_per_market(
    session: Session, market_ids: list[uuid.UUID]
) -> dict[uuid.UUID, models.Estimate]:
    if not market_ids:
        return {}
    # Anti-lookahead: drop estimates on a post-resolution snapshot, then keep the latest per market.
    rows = session.exec(
        select(models.Estimate)
        .join(
            models.MarketSnapshot,
            col(models.MarketSnapshot.id) == col(models.Estimate.snapshot_id),
        )
        .join(
            models.Resolution,
            col(models.Resolution.market_id) == col(models.Estimate.market_id),
        )
        .where(col(models.Estimate.market_id).in_(market_ids))
        .where(col(models.MarketSnapshot.ts) <= col(models.Resolution.resolved_at))
        .distinct(col(models.Estimate.market_id))
        .order_by(
            col(models.Estimate.market_id),
            col(models.Estimate.created_at).desc(),
            col(models.Estimate.id).desc(),
        )
    ).all()
    return {row.market_id: row for row in rows}


def _categories_of(session: Session, market_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not market_ids:
        return {}
    rows = session.exec(select(models.Market).where(col(models.Market.id).in_(market_ids))).all()
    return {row.id: row.category for row in rows if row.id is not None}


def _markets_of(session: Session, market_ids: list[uuid.UUID]) -> dict[uuid.UUID, models.Market]:
    if not market_ids:
        return {}
    rows = session.exec(select(models.Market).where(col(models.Market.id).in_(market_ids))).all()
    return {row.id: row for row in rows if row.id is not None}


def _horizon(market: models.Market, ts: datetime) -> float | None:
    deadline = market.resolves_at or market.close_time
    if deadline is None:
        return None
    return (deadline - ts).total_seconds() / 86_400


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    categories: list[CategoryCalibration]
    reliability: list[ReliabilityBin]
    ev: EvCalibration | None
    cohorts: list[CohortCalibration]


def gate_thresholds_from_settings(settings: Settings) -> GateThresholds:
    return GateThresholds(
        min_n=settings.gate_min_n,
        brier_margin=settings.gate_brier_margin,
        ev_tolerance=settings.gate_ev_tolerance,
        max_invalid_rate=settings.max_category_invalid_rate,
    )


class CalibrationService:
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def report(self, *, n_bins: int = DEFAULT_RELIABILITY_BINS) -> CalibrationReport:
        outcomes = self._resolved_outcomes()
        by_category, by_cohort = self._records(outcomes)
        return self._assemble(by_category, by_cohort, outcomes, n_bins)

    def blended_report(self, *, n_bins: int = DEFAULT_RELIABILITY_BINS) -> CalibrationReport:
        outcomes = self._resolved_outcomes()
        by_category, by_cohort = self._blended_records(outcomes)
        return self._assemble(by_category, by_cohort, outcomes, n_bins)

    def gate_status(self, thresholds: GateThresholds) -> list[GateDecision]:
        # The §8.5 gate per category; ev is system-wide today (per-category EV is a future
        # refinement). A category clears only on measured calibration — never wires execution.
        report = self.report()
        return [evaluate_gate(category, report.ev, thresholds) for category in report.categories]

    def _assemble(
        self,
        records: _Records,
        by_cohort: _Records,
        outcomes: dict[uuid.UUID, int],
        n_bins: int,
    ) -> CalibrationReport:
        forecasts = [p_model for samples in records.values() for (p_model, _q, _o) in samples]
        observed = [outcome for samples in records.values() for (_p, _q, outcome) in samples]
        invalid_counts = ResolutionRepository(self._session).invalid_counts_by_category()
        return CalibrationReport(
            categories=calibration_by_category(records, invalid_counts),
            reliability=reliability_bins(forecasts, observed, n_bins) if forecasts else [],
            ev=self._ev(outcomes),
            cohorts=calibration_by_cohort(by_cohort),
        )

    def records(self) -> _Records:
        by_category, _by_cohort = self._records(self._resolved_outcomes())
        return by_category

    def _blended_records(self, outcomes: dict[uuid.UUID, int]) -> tuple[_Records, _Records]:
        latest = _latest_estimate_per_market(self._session, list(outcomes))
        categories = _categories_of(self._session, list(latest))
        market_rows = _markets_of(self._session, list(latest))
        snapshots = self._snapshots([estimate.snapshot_id for estimate in latest.values()])
        asof_by_market = {
            str(market_id): snapshots[estimate.snapshot_id][1]
            for market_id, estimate in latest.items()
        }
        all_quotes = VenueQuoteRepository(self._session).retrieve_for_markets(asof_by_market)
        policy = cohort_policy(self._settings)
        by_category: _Records = {}
        by_cohort: _Records = {}
        for market_id, estimate in latest.items():
            q_market, ts = snapshots[estimate.snapshot_id]
            quotes = all_quotes.get(str(market_id), [])
            q_blended = blended_baseline(q_market, latest_prices_per_venue(quotes))
            # Brier baseline column uses q_blended; cohort classification bins on q_market
            record: tuple[float, float, int] = (estimate.p_model, q_blended, outcomes[market_id])
            by_category.setdefault(categories[market_id], []).append(record)
            market = market_rows.get(market_id)
            h = _horizon(market, ts) if market is not None else None
            for c in cohorts_for(price=q_market, horizon_days=h, policy=policy):
                by_cohort.setdefault(c.value, []).append(record)
            by_cohort.setdefault(Cohort.OVERALL.value, []).append(record)
        return by_category, by_cohort

    def _snapshots(self, snapshot_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[float, datetime]]:
        if not snapshot_ids:
            return {}
        rows = self._session.exec(
            select(models.MarketSnapshot).where(col(models.MarketSnapshot.id).in_(snapshot_ids))
        ).all()
        return {row.id: (row.yes_price, row.ts) for row in rows if row.id is not None}

    def _records(self, outcomes: dict[uuid.UUID, int]) -> tuple[_Records, _Records]:
        latest = _latest_estimate_per_market(self._session, list(outcomes))
        categories = _categories_of(self._session, list(latest))
        market_rows = _markets_of(self._session, list(latest))
        snapshots = self._snapshots([estimate.snapshot_id for estimate in latest.values()])
        policy = cohort_policy(self._settings)
        by_category: _Records = {}
        by_cohort: _Records = {}
        for market_id, estimate in latest.items():
            q_market, ts = snapshots[estimate.snapshot_id]
            record: tuple[float, float, int] = (estimate.p_model, q_market, outcomes[market_id])
            by_category.setdefault(categories[market_id], []).append(record)
            market = market_rows.get(market_id)
            h = _horizon(market, ts) if market is not None else None
            for c in cohorts_for(price=q_market, horizon_days=h, policy=policy):
                by_cohort.setdefault(c.value, []).append(record)
            by_cohort.setdefault(Cohort.OVERALL.value, []).append(record)
        return by_category, by_cohort

    def category_brier_deltas(self, category: str) -> list[float]:
        """Chronological per-resolution `brier_market - brier_system` for a category (drift input).

        Positive means the system beat the market on that resolution; a downward trend is drift.
        """
        resolutions = self._session.exec(
            select(models.Resolution)
            .join(models.Market, col(models.Resolution.market_id) == col(models.Market.id))
            .where(col(models.Resolution.ambiguous).is_(False))
            .where(col(models.Market.category) == category)
            .order_by(col(models.Resolution.resolved_at), col(models.Resolution.id))
        ).all()
        ordered = [(row.market_id, row.outcome) for row in resolutions]
        latest = _latest_estimate_per_market(self._session, [market_id for market_id, _ in ordered])
        prices = self._snapshot_prices([estimate.snapshot_id for estimate in latest.values()])
        deltas: list[float] = []
        for market_id, outcome in ordered:
            estimate = latest.get(market_id)
            if estimate is None:
                continue
            q = prices[estimate.snapshot_id]
            deltas.append(brier(q, outcome) - brier(estimate.p_model, outcome))
        return deltas

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
        resolution_id: str | None = None,
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
                id=deterministic_id("postmortem", estimate.id),
                market_id=estimate.market_id,
                category=category,
                estimate_id=estimate.id,
                resolution_id=resolution_id,
                failure_class=verdict.failure_class,
                brier=brier(estimate.p_model, outcome),
                rationale=verdict.rationale,
                evidence_ids=verdict.evidence_ids,
            )
        )
        action = suggested_action(verdict.failure_class)
        if action is not None:
            self._candidates.add(
                ImprovementCandidate(
                    id=deterministic_id("candidate", postmortem.id, action.value),
                    postmortem_id=postmortem.id,
                    kind=action,
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
            min_n=self._settings.improvement_validation_min_n,
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
