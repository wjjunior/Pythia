"""Plain-Python orchestrator (§2.3, §3.6): research → estimate → critic → score → risk.

The integration hub. It owns the point-in-time boundary (`asof = snapshot.ts`), runs the mandatory
critic before scoring, keeps the LLM off the deterministic scoring/risk path, persists the Score,
and writes an append-only `audit_event` at every step under one `pipeline_run`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlmodel import Session

from pythia.agents import CriticAgent
from pythia.common import (
    AuditEvent,
    AuditKind,
    Estimate,
    EstimationContext,
    Evidence,
    EvidencePort,
    LLMPort,
    Market,
    MarketSnapshot,
    Score,
)
from pythia.common.logging import bind, bind_correlation, clear_correlation, get_logger
from pythia.config import Settings
from pythia.db.repositories import (
    AuditRepository,
    EstimateMemberRepository,
    EstimateRepository,
    PipelineRunRepository,
    RecalibrationRepository,
    ResolutionRepository,
    ScoreRepository,
    SourceRepository,
    VenueQuoteRepository,
)
from pythia.eval.service import CalibrationService, gate_thresholds_from_settings
from pythia.portfolio.service import PortfolioService
from pythia.risk import (
    ExposureState,
    RiskDecision,
    RiskLimits,
    RiskReason,
    TradeProposal,
    evaluate,
    grade_p_resol,
    invalid_rate,
    is_resolution_gated,
)
from pythia.scoring import (
    build_score,
    classify_resolution_source,
    market_disagreement_penalty,
    position_size,
    resolution_penalty,
)
from pythia.scoring.divergence import divergence, divergence_penalty, latest_prices_per_venue

DEFAULT_RESEARCH_K = 8
NEUTRAL_PENALTY = 1.0
DEFAULT_FEE = 0.0
DEFAULT_SLIPPAGE = 0.0

_log = get_logger(__name__)


class PipelineRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    run_id: str
    estimate: Estimate | None
    score: Score | None
    decision: RiskDecision


class Pipeline:
    def __init__(
        self,
        *,
        session: Session,
        llm: LLMPort,
        evidence: EvidencePort,
        settings: Settings,
        routing: Mapping[str, str],
        k: int = DEFAULT_RESEARCH_K,
    ) -> None:
        self._llm = llm
        self._critic = CriticAgent(llm)
        self._evidence = evidence
        self._settings = settings
        self._routing = dict(routing)
        self._k = k
        self._session = session
        self._estimates = EstimateRepository(session)
        self._members = EstimateMemberRepository(session)
        self._recalibration = RecalibrationRepository(session)
        self._resolutions = ResolutionRepository(session)
        self._venue_quotes = VenueQuoteRepository(session)
        self._scores = ScoreRepository(session)
        self._runs = PipelineRunRepository(session)
        self._audit = AuditRepository(session)
        self._sources = SourceRepository(session)

    def run(self, market: Market, snapshot: MarketSnapshot) -> PipelineResult:
        run_id = self._runs.open(market.id, self._routing)
        bind_correlation(run_id, market_id=market.id)
        _log.info(
            "pipeline_run_start", market_external_id=market.external_id, snapshot_id=snapshot.id
        )
        try:
            return self._run(run_id, market, snapshot)
        except Exception:
            self._session.rollback()
            self._runs.finish(run_id, status=PipelineRunStatus.FAILED)
            _log.exception("pipeline_run_failed")
            raise
        finally:
            clear_correlation()

    def _run(self, run_id: str, market: Market, snapshot: MarketSnapshot) -> PipelineResult:
        portfolio = PortfolioService(self._session, self._settings)
        capital = portfolio.capital()
        exposure = portfolio.exposure_state()
        limits = self._risk_limits(capital.total_capital)

        if exposure.per_category.get(market.category, 0.0) >= limits.cap_per_category:
            return self._veto_before_estimation(run_id, market)

        evidence = self._evidence.retrieve(market.id, snapshot.ts, self._k)

        estimate = self._llm.estimate(
            EstimationContext(market=market, snapshot=snapshot, evidence=evidence)
        )
        bind(estimate_id=estimate.id)
        _log.info(
            "estimate",
            p_model=estimate.p_model,
            confidence=estimate.confidence,
            model=estimate.model_name,
        )
        self._audit.append(
            AuditEvent(
                kind=AuditKind.ESTIMATE,
                market_id=market.id,
                payload={
                    "run_id": run_id,
                    "estimate_id": estimate.id,
                    "p_model": estimate.p_model,
                    "confidence": estimate.confidence,
                    "model": estimate.model_name,
                },
            )
        )

        critiqued = self._critic.review(estimate, evidence)
        # The deterministic layer sizes on the recalibrated, market-distrust-adjusted forecast.
        # Persist that committed p_model on the estimate so the calibration verdict measures the
        # forecast the system actually acts on, not the raw estimator output (PYT-100, §4.9).
        scored, _recalibrated = self._recalibrated(market.category, critiqued)
        scored = self._distrust_market_disagreement(scored, snapshot)
        committed = critiqued.model_copy(update={"p_model_calibrated": scored.p_model})
        self._estimates.add(committed)
        self._members.add_many(committed.id, committed.members)
        _log.info("critic_verdict", confidence=critiqued.confidence)
        self._audit.append(
            AuditEvent(
                kind=AuditKind.CRITIC_VERDICT,
                market_id=market.id,
                payload={
                    "run_id": run_id,
                    "estimate_id": critiqued.id,
                    "confidence": critiqued.confidence,
                    "critic_notes": critiqued.critic_notes,
                },
            )
        )

        graded_p_resol, resolution_gated = self._resolution_risk(market, snapshot)
        p_corr = self._venue_p_corr(market, snapshot)
        # `paper_only` is driven by the §8.5 gate (market superiority over min-n), not by the mere
        # existence of a recalibration map — a category with a map that fails the Brier gate stays
        # gated (PYT-101). The category being scored is unresolved, so it is never in the gate set.
        category_cleared = self._category_cleared(market.category)
        score = self._score(scored, snapshot, graded_p_resol, p_corr)
        self._scores.add(score)
        _log.info(
            "score",
            opportunity_score=score.opportunity_score,
            edge_net=score.edge_net,
            p_calibrated=scored.p_model,
        )
        self._audit.append(
            AuditEvent(
                kind=AuditKind.SCORE,
                market_id=market.id,
                payload={
                    "run_id": run_id,
                    "estimate_id": critiqued.id,
                    "opportunity_score": score.opportunity_score,
                    "edge_net": score.edge_net,
                    "p_calibrated": scored.p_model,
                    "category_cleared": category_cleared,
                },
            )
        )

        decision = self._evaluate_risk(
            market,
            scored,
            score,
            evidence,
            capital.available_capital,
            exposure,
            limits,
            category_cleared,
            resolution_gated,
        )
        _log.info(
            "risk_decision",
            approved_size=decision.approved_size,
            vetoed=decision.vetoed,
            paper_only=decision.paper_only,
        )
        self._audit.append(
            AuditEvent(
                kind=AuditKind.RISK_DECISION,
                market_id=market.id,
                payload={
                    "run_id": run_id,
                    "estimate_id": critiqued.id,
                    "approved_size": decision.approved_size,
                    "vetoed": decision.vetoed,
                    "paper_only": decision.paper_only,
                    "reasons": [reason.value for reason in decision.reasons],
                },
            )
        )

        self._runs.finish(run_id, status=PipelineRunStatus.COMPLETED)
        _log.info("pipeline_run_finish")
        return PipelineResult(run_id=run_id, estimate=committed, score=score, decision=decision)

    def _veto_before_estimation(self, run_id: str, market: Market) -> PipelineResult:
        decision = RiskDecision(
            approved_size=0.0,
            vetoed=True,
            reasons=(RiskReason.CAP_PER_CATEGORY,),
            paper_only=True,
        )
        _log.info("risk_decision", approved_size=0.0, vetoed=True, paper_only=True)
        self._audit.append(
            AuditEvent(
                kind=AuditKind.RISK_DECISION,
                market_id=market.id,
                payload={
                    "run_id": run_id,
                    "estimate_id": None,
                    "approved_size": 0.0,
                    "vetoed": True,
                    "paper_only": True,
                    "reasons": [RiskReason.CAP_PER_CATEGORY.value],
                },
            )
        )
        self._runs.finish(run_id, status=PipelineRunStatus.COMPLETED)
        _log.info("pipeline_run_finish")
        return PipelineResult(run_id=run_id, estimate=None, score=None, decision=decision)

    def _recalibrated(self, category: str, estimate: Estimate) -> tuple[Estimate, bool]:
        active = self._recalibration.active_for_category(category)
        if active is None:
            return estimate, False
        cmap, n_fit = active
        if n_fit < self._settings.recalibration_min_n:
            return estimate, False
        return estimate.model_copy(update={"p_model": cmap.apply(estimate.p_model)}), True

    def _category_cleared(self, category: str) -> bool:
        # Has this category cleared the §8.5 gate (Brier(system) < Brier(market) by margin over
        # min-n, EV honest, resolution quality ok)? Drives `paper_only`. Measured on the committed
        # forecast (PYT-100) over only resolved markets, so the market being scored is never in it.
        service = CalibrationService(self._session, settings=self._settings)
        thresholds = gate_thresholds_from_settings(self._settings)
        return any(
            decision.category == category and decision.cleared
            for decision in service.gate_status(thresholds)
        )

    def _distrust_market_disagreement(
        self, estimate: Estimate, snapshot: MarketSnapshot
    ) -> Estimate:
        penalty = market_disagreement_penalty(estimate.p_model, snapshot.yes_price)
        if penalty >= 1.0:
            return estimate
        _log.info(
            "market_disagreement_distrust",
            p_model=estimate.p_model,
            yes_price=snapshot.yes_price,
            penalty=penalty,
        )
        return estimate.model_copy(update={"confidence": estimate.confidence * penalty})

    def _score(
        self, estimate: Estimate, snapshot: MarketSnapshot, p_resol: float, p_corr: float
    ) -> Score:
        return build_score(
            estimate,
            snapshot.yes_price,
            spread=snapshot.spread,
            fee=DEFAULT_FEE,
            slippage=DEFAULT_SLIPPAGE,
            p_resol=p_resol,
            p_liq=NEUTRAL_PENALTY,
            p_time=NEUTRAL_PENALTY,
            p_corr=p_corr,
        )

    def _venue_p_corr(self, market: Market, snapshot: MarketSnapshot) -> float:
        quotes = self._venue_quotes.retrieve(market.id, asof=snapshot.ts)
        spread = divergence(snapshot.yes_price, latest_prices_per_venue(quotes))
        return divergence_penalty(spread, self._settings.divergence_penalty_weight)

    def _resolution_risk(self, market: Market, snapshot: MarketSnapshot) -> tuple[float, bool]:
        since = snapshot.ts - timedelta(days=self._settings.dispute_risk_lookback)
        invalid, total = self._resolutions.dispute_counts(
            market.category, since=since, asof=snapshot.ts
        )
        rate = invalid_rate(invalid, total)
        base = resolution_penalty(classify_resolution_source(market.resolution_source))
        gated = is_resolution_gated(rate, self._settings.max_category_invalid_rate)
        return grade_p_resol(base, rate), gated

    def _evaluate_risk(
        self,
        market: Market,
        estimate: Estimate,
        score: Score,
        evidence: list[Evidence],
        available: float,
        exposure: ExposureState,
        limits: RiskLimits,
        category_calibrated: bool,
        resolution_gated: bool,
    ) -> RiskDecision:
        proposed = position_size(
            available,
            score.kelly_fraction,
            self._settings.kelly_mult,
            estimate.confidence,
            limits.cap_per_trade,
        )
        proposal = TradeProposal(
            proposed_size=proposed,
            category=market.category,
            correlation_group=market.id,
            confidence=estimate.confidence,
            source_reliability=self._source_reliability(evidence),
            p_resol_severity=1.0 - score.p_resol,
            opportunity_score=score.opportunity_score,
            category_calibrated=category_calibrated,
            resolution_gated=resolution_gated,
        )
        return evaluate(proposal, limits, exposure)

    def _risk_limits(self, capital: float) -> RiskLimits:
        settings = self._settings
        return RiskLimits(
            cap_per_trade=capital * settings.cap_per_trade,
            cap_per_market=capital * settings.cap_per_market,
            cap_per_category=capital * settings.cap_per_category,
            cap_daily=capital * settings.cap_daily,
            cap_weekly=capital * settings.cap_weekly,
            cap_absolute=capital * settings.cap_absolute,
            min_source_reliability=settings.min_source_reliability,
            min_confidence=settings.min_confidence,
            max_p_resol_severity=settings.max_p_resol_severity,
        )

    def _source_reliability(self, evidence: list[Evidence]) -> float:
        reliabilities = [
            source.reliability
            for source_id in {item.source_id for item in evidence}
            if (source := self._sources.get(source_id)) is not None
        ]
        return max(reliabilities, default=0.0)
