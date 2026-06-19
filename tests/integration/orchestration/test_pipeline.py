"""End-to-end orchestration on real pgvector with the zero-secret stub (§2.3, §3.6)."""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import structlog
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
    MemberForecast,
    Source,
)
from pythia.common.logging import clear_correlation, configure_logging
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    AuditRepository,
    EstimateMemberRepository,
    EstimateRepository,
    MarketRepository,
    RecalibrationRepository,
    ScoreRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.eval.recalibration import CalibrationMap
from pythia.orchestration import Pipeline, PipelineRunStatus
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.risk import RiskReason

pytestmark = pytest.mark.integration

_LIFECYCLE_EVENTS = {
    "pipeline_run_start",
    "estimate",
    "critic_verdict",
    "score",
    "risk_decision",
    "pipeline_run_finish",
}


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    clear_correlation()


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


def _seed_disputes(
    session: Session, category: str, *, ambiguous: int, valid: int, resolved_at: datetime
) -> None:
    for index in range(ambiguous + valid):
        name = f"dispute-{category}-{index}"
        market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
        MarketRepository(session).upsert(
            Market(
                id=market_id,
                external_id=name,
                question="q",
                category=category,
                resolution_rule="r",
            )
        )
        session.add(
            models.Resolution(
                market_id=uuid.UUID(market_id),
                outcome=0,
                resolved_at=resolved_at,
                ambiguous=index < ambiguous,
            )
        )
        session.commit()


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


class _EnsembleLLM:
    def estimate(self, ctx: EstimationContext) -> Estimate:
        return Estimate(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"ensemble-{ctx.snapshot.id}")),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=0.7,
            confidence=0.9,
            lower=0.6,
            upper=0.8,
            rationale="ensemble median of 2 members",
            evidence_ids=[item.id for item in ctx.evidence],
            model_name="ensemble",
            members=[
                MemberForecast(model="a/m1", p_member=0.6),
                MemberForecast(model="b/m2", p_member=0.8),
            ],
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

    assert result.estimate is not None
    assert result.score is not None
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


def test_one_correlation_id_reconstructs_the_run_lifecycle(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(service="pipeline-test")
    market, snapshot = _seed(session)
    result = _pipeline(session, StubLLMPort()).run(market, snapshot)

    records = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    lifecycle = [r for r in records if r["event"] in _LIFECYCLE_EVENTS]
    assert {r["event"] for r in lifecycle} == _LIFECYCLE_EVENTS
    assert {r["correlation_id"] for r in lifecycle} == {result.run_id}


def test_pipeline_persists_ensemble_members(session: Session) -> None:
    market, snapshot = _seed(session)
    result = _pipeline(session, _EnsembleLLM()).run(market, snapshot)
    assert result.estimate is not None
    stored = EstimateMemberRepository(session).list_for_estimate(result.estimate.id)
    assert {member.model for member in stored} == {"a/m1", "b/m2"}


def test_pipeline_scores_on_the_recalibrated_probability(session: Session) -> None:
    market, snapshot = _seed(session)
    RecalibrationRepository(session).save(
        "politics",
        CalibrationMap(method="isotonic", params={"x": [0.0, 1.0], "y": [0.55, 0.65]}),
        n_fit=100,
    )
    result = _pipeline(session, _ConfidentLLM()).run(market, snapshot)
    assert result.estimate is not None
    assert result.score is not None
    # The estimate row keeps the raw p_model; the score uses p_calibrated = interp(0.7) = 0.62.
    assert result.estimate.p_model == 0.7
    assert result.score.edge == pytest.approx(0.62 - 0.5)


def test_pipeline_ignores_a_below_min_n_recalibration_map(session: Session) -> None:
    market, snapshot = _seed(session)
    RecalibrationRepository(session).save(
        "politics",
        CalibrationMap(method="isotonic", params={"x": [0.0, 1.0], "y": [0.55, 0.65]}),
        n_fit=1,
    )
    result = _pipeline(session, _ConfidentLLM()).run(market, snapshot)
    assert result.score is not None
    # n_fit=1 is below the default min_n: the raw p_model (0.7) is scored, not p_calibrated.
    assert result.score.edge == pytest.approx(0.7 - 0.5)


def test_pipeline_sizes_down_a_high_invalid_rate_category(session: Session) -> None:
    market, snapshot = _seed(session)  # politics, UMA source → base P_resol 0.6
    _seed_disputes(session, "politics", ambiguous=2, valid=2, resolved_at=ASOF - timedelta(days=5))
    result = _pipeline(session, _ConfidentLLM()).run(market, snapshot)
    assert result.score is not None
    # invalid_rate 0.5 grades 0.6 → 0.3; severity 0.7 > max_p_resol_severity 0.5 → vetoed.
    assert result.score.p_resol == pytest.approx(0.3)
    assert result.decision.vetoed
    assert RiskReason.MAX_P_RESOL_SEVERITY in result.decision.reasons


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
    assert result.score is not None
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
