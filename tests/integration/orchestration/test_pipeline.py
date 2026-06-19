"""End-to-end orchestration on real pgvector with the zero-secret stub (§2.3, §3.6)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from pythia.agents import StubLLMPort
from pythia.common import (
    AuditKind,
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    Market,
    MarketSnapshot,
    Source,
)
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    AuditRepository,
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.orchestration import Pipeline, PipelineRunStatus
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.risk import RiskReason

pytestmark = pytest.mark.integration

QUESTION = "Will candidate Alpha win the 2026 governor election?"
MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt20-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt20-snapshot"))
SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt20-source"))
ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
ROUTING = {"estimation": "stub", "critic": "stub"}


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _seed(session: Session) -> tuple[Market, MarketSnapshot]:
    market = MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt20",
            question=QUESTION,
            category="politics",
            resolution_rule="UMA optimistic oracle",
            resolution_source="UMA",
        )
    )
    snapshot = SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=ASOF,
            yes_price=0.5,
            spread=0.02,
            volume=1000.0,
            liquidity=5000.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_ID, name="reuters", kind="news", reliability=0.9)
    )
    EvidenceStore(session, StubEmbeddingPort()).add(
        market_id=MARKET_ID,
        source_id=SOURCE_ID,
        observed_at=ASOF - timedelta(days=1),
        content="Polls show candidate Alpha leading the 2026 governor election.",
    )
    return market, snapshot


def _pipeline(session: Session, llm: object) -> Pipeline:
    return Pipeline(
        session=session,
        llm=llm,  # type: ignore[arg-type]
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=_settings(),
        routing=ROUTING,
    )


class _ConfidentLLM:
    def estimate(self, ctx: EstimationContext) -> Estimate:
        return Estimate(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"confident-{ctx.snapshot.id}")),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=0.7,
            confidence=0.9,
            lower=0.6,
            upper=0.8,
            rationale="confident",
            evidence_ids=[item.id for item in ctx.evidence],
            model_name="fake/confident",
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        return CriticVerdict(
            adjusted_confidence=0.8, objections=[], lookahead_leak=False, notes="ok"
        )


class _BrokenLLM:
    def estimate(self, ctx: EstimationContext) -> Estimate:
        raise RuntimeError("estimation boom")

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        raise NotImplementedError


def test_pipeline_scores_persists_and_audits_every_step(session: Session) -> None:
    market, snapshot = _seed(session)
    result = _pipeline(session, StubLLMPort()).run(market, snapshot)

    persisted_score = ScoreRepository(session).get_by_estimate(result.estimate.id)
    assert persisted_score is not None
    assert persisted_score.opportunity_score == result.score.opportunity_score
    assert EstimateRepository(session).get(result.estimate.id) is not None

    run = session.get(models.PipelineRun, uuid.UUID(result.run_id))
    assert run is not None
    assert run.routing == ROUTING
    assert run.status == PipelineRunStatus.COMPLETED
    assert run.finished_at is not None

    kinds = {event.kind for event in AuditRepository(session).list_for_market(MARKET_ID)}
    assert kinds == {
        AuditKind.ESTIMATE,
        AuditKind.CRITIC_VERDICT,
        AuditKind.SCORE,
        AuditKind.RISK_DECISION,
    }


def test_pipeline_vetoes_the_low_confidence_stub(session: Session) -> None:
    market, snapshot = _seed(session)
    result = _pipeline(session, StubLLMPort()).run(market, snapshot)
    assert result.decision.vetoed
    assert RiskReason.MIN_CONFIDENCE in result.decision.reasons


def test_pipeline_sizes_a_confident_paper_only_opportunity(session: Session) -> None:
    market, snapshot = _seed(session)
    result = _pipeline(session, _ConfidentLLM()).run(market, snapshot)
    assert not result.decision.vetoed
    assert result.decision.approved_size > 0.0
    assert result.decision.paper_only
    assert result.score.edge_net == pytest.approx(0.7 - 0.5 - 0.02 / 2)


def test_pipeline_marks_the_run_failed_on_error(session: Session) -> None:
    market, snapshot = _seed(session)
    with pytest.raises(RuntimeError):
        _pipeline(session, _BrokenLLM()).run(market, snapshot)
    runs = session.exec(
        select(models.PipelineRun).where(models.PipelineRun.market_id == uuid.UUID(MARKET_ID))
    ).all()
    assert len(runs) == 1
    assert runs[0].status == PipelineRunStatus.FAILED
    assert runs[0].finished_at is not None
