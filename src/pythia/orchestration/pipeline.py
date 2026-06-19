"""Plain-Python orchestrator (§2.3, §3.6): research → estimate → critic → score → risk.

The integration hub. It owns the point-in-time boundary (`asof = snapshot.ts`), runs the mandatory
critic before scoring, keeps the LLM off the deterministic scoring/risk path, persists the Score,
and writes an append-only `audit_event` at every step under one `pipeline_run`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
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
from pythia.config import Settings
from pythia.db.repositories import (
    AuditRepository,
    EstimateRepository,
    PipelineRunRepository,
    ScoreRepository,
    SourceRepository,
)
from pythia.risk import ExposureState, RiskDecision, RiskLimits, TradeProposal, evaluate
from pythia.scoring import (
    build_score,
    classify_resolution_source,
    position_size,
    resolution_penalty,
)

DEFAULT_RESEARCH_K = 8
NEUTRAL_PENALTY = 1.0
DEFAULT_FEE = 0.0
DEFAULT_SLIPPAGE = 0.0

_EMPTY_EXPOSURE = ExposureState(
    per_group={}, per_category={}, daily_total=0.0, weekly_total=0.0, absolute_total=0.0
)


class PipelineRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    run_id: str
    estimate: Estimate
    score: Score
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
        self._estimates = EstimateRepository(session)
        self._scores = ScoreRepository(session)
        self._runs = PipelineRunRepository(session)
        self._audit = AuditRepository(session)
        self._sources = SourceRepository(session)

    def run(self, market: Market, snapshot: MarketSnapshot) -> PipelineResult:
        run_id = self._runs.open(market.id, self._routing)

        evidence = self._evidence.retrieve(market.id, snapshot.ts, self._k)

        estimate = self._llm.estimate(
            EstimationContext(market=market, snapshot=snapshot, evidence=evidence)
        )
        self._audit.append(
            AuditEvent(
                kind=AuditKind.ESTIMATE,
                market_id=market.id,
                payload={
                    "estimate_id": estimate.id,
                    "p_model": estimate.p_model,
                    "confidence": estimate.confidence,
                    "model": estimate.model_name,
                },
            )
        )

        critiqued = self._critic.review(estimate, evidence)
        self._estimates.add(critiqued)
        self._audit.append(
            AuditEvent(
                kind=AuditKind.CRITIC_VERDICT,
                market_id=market.id,
                payload={
                    "estimate_id": critiqued.id,
                    "confidence": critiqued.confidence,
                    "critic_notes": critiqued.critic_notes,
                },
            )
        )

        score = self._score(market, critiqued, snapshot)
        self._scores.add(score)
        self._audit.append(
            AuditEvent(
                kind=AuditKind.SCORE,
                market_id=market.id,
                payload={
                    "estimate_id": critiqued.id,
                    "opportunity_score": score.opportunity_score,
                    "edge_net": score.edge_net,
                },
            )
        )

        decision = self._evaluate_risk(market, critiqued, score, evidence)
        self._audit.append(
            AuditEvent(
                kind=AuditKind.RISK_DECISION,
                market_id=market.id,
                payload={
                    "estimate_id": critiqued.id,
                    "approved_size": decision.approved_size,
                    "vetoed": decision.vetoed,
                    "paper_only": decision.paper_only,
                    "reasons": [reason.value for reason in decision.reasons],
                },
            )
        )

        self._runs.finish(run_id, status=PipelineRunStatus.COMPLETED)
        return PipelineResult(run_id=run_id, estimate=critiqued, score=score, decision=decision)

    def _score(self, market: Market, estimate: Estimate, snapshot: MarketSnapshot) -> Score:
        p_resol = resolution_penalty(classify_resolution_source(market.resolution_source))
        return build_score(
            estimate,
            snapshot.yes_price,
            spread=snapshot.spread,
            fee=DEFAULT_FEE,
            slippage=DEFAULT_SLIPPAGE,
            p_resol=p_resol,
            p_liq=NEUTRAL_PENALTY,
            p_time=NEUTRAL_PENALTY,
            p_corr=NEUTRAL_PENALTY,
        )

    def _evaluate_risk(
        self, market: Market, estimate: Estimate, score: Score, evidence: list[Evidence]
    ) -> RiskDecision:
        available = self._settings.bankroll_paper
        limits = self._risk_limits(available)
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
            correlation_group=market.category,
            confidence=estimate.confidence,
            source_reliability=self._source_reliability(evidence),
            p_resol_severity=1.0 - score.p_resol,
            category_calibrated=False,
        )
        return evaluate(proposal, limits, _EMPTY_EXPOSURE)

    def _risk_limits(self, available: float) -> RiskLimits:
        settings = self._settings
        return RiskLimits(
            cap_per_trade=available * settings.cap_per_trade,
            cap_per_market=available * settings.cap_per_market,
            cap_per_category=available * settings.cap_per_category,
            cap_daily=available * settings.cap_daily,
            cap_weekly=available * settings.cap_weekly,
            cap_absolute=available * settings.cap_absolute,
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
