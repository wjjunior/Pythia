"""Integration test: liquidity_penalty wired into pipeline → Score.p_liq (PYT-189)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import (
    CriticVerdict,
    Estimate,
    EstimationContext,
    Evidence,
    Market,
    MarketSnapshot,
    SignalSource,
    Source,
)
from pythia.config import Settings
from pythia.db.repositories import (
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.orchestration import Pipeline
from pythia.rag import EvidenceStore, StubEmbeddingPort

pytestmark = pytest.mark.integration

ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_ROUTING = {"estimation": "stub", "critic": "stub"}


class _ConfidentLLM:
    """Returns a high-confidence estimate above the market price, producing positive EV."""

    def estimate(self, ctx: EstimationContext) -> Estimate:
        return Estimate(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"pliq-confident-{ctx.snapshot.id}")),
            market_id=ctx.market.id,
            snapshot_id=ctx.snapshot.id,
            p_model=0.7,
            confidence=0.9,
            lower=0.6,
            upper=0.8,
            rationale="confident",
            evidence_ids=[item.id for item in ctx.evidence],
            model_name="fake/confident",
            signal_source=SignalSource.UNKNOWN,
        )

    def critique(self, est: Estimate, evidence: list[Evidence]) -> CriticVerdict:
        return CriticVerdict(
            adjusted_confidence=0.8, objections=[], lookahead_leak=False, notes="ok"
        )


def _settings(liquidity_ref: float = 10_000.0) -> Settings:
    return Settings(
        _env_file=None, database_url="postgresql+psycopg://x/y", liquidity_ref=liquidity_ref
    )


def _seed_market(session: Session, *, key: str, liquidity: float) -> tuple[Market, MarketSnapshot]:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt189-{key}"))
    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt189-{key}-src"))
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt189-{key}-snap"))

    market = MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"pyt189-{key}",
            question="Will X happen?",
            category="politics",
            resolution_rule="UMA",
            resolution_source="UMA",
        )
    )
    snapshot = SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=ASOF,
            yes_price=0.5,
            spread=0.02,
            volume=1000.0,
            liquidity=liquidity,
        )
    )
    SourceRepository(session).upsert(
        Source(id=source_id, name=f"source-{key}", kind="news", reliability=0.9)
    )
    EvidenceStore(session, StubEmbeddingPort()).add(
        market_id=market_id,
        source_id=source_id,
        observed_at=ASOF - timedelta(days=1),
        content="Evidence for the market outcome.",
    )
    return market, snapshot


def _pipeline(session: Session, liquidity_ref: float = 10_000.0) -> Pipeline:
    return Pipeline(
        session=session,
        llm=_ConfidentLLM(),  # type: ignore[arg-type]
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=_settings(liquidity_ref=liquidity_ref),
        routing=_ROUTING,
        source=SignalSource.RADAR,
    )


def test_p_liq_below_ref_is_less_than_one(session: Session) -> None:
    # A snapshot with liquidity < liquidity_ref must produce p_liq < 1.0 in the persisted Score.
    market, snapshot = _seed_market(session, key="thin", liquidity=2_500.0)
    result = _pipeline(session, liquidity_ref=10_000.0).run(market, snapshot)

    assert result.score is not None
    assert result.estimate is not None
    persisted = ScoreRepository(session).get_by_estimate(result.estimate.id)
    assert persisted is not None
    assert persisted.p_liq == pytest.approx(0.25)
    assert persisted.p_liq < 1.0


def test_p_liq_scales_opportunity_score(session: Session) -> None:
    # The opportunity_score on the thin-liquidity market must be lower than on the full-liquidity
    # market (all other factors equal), proving p_liq reaches the final metric end-to-end.
    thin_market, thin_snap = _seed_market(session, key="thin2", liquidity=2_500.0)
    full_market, full_snap = _seed_market(session, key="full2", liquidity=10_000.0)

    pipe = _pipeline(session, liquidity_ref=10_000.0)
    thin_result = pipe.run(thin_market, thin_snap)
    full_result = pipe.run(full_market, full_snap)

    assert thin_result.score is not None and full_result.score is not None
    assert thin_result.score.p_liq == pytest.approx(0.25)
    assert full_result.score.p_liq == pytest.approx(1.0)
    assert thin_result.score.opportunity_score < full_result.score.opportunity_score
