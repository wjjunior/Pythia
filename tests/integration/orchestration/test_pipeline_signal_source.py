"""PYT-188 T4: Pipeline stamps signal_source on persisted estimate and score."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.agents import StubLLMPort
from pythia.common import (
    Market,
    MarketSnapshot,
    SignalSource,
    Source,
)
from pythia.config import Settings
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.orchestration import Pipeline
from pythia.rag import EvidenceStore, StubEmbeddingPort

pytestmark = pytest.mark.integration

ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
ROUTING = {"estimation": "stub", "critic": "stub"}


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _seed(session: Session, *, key: str) -> tuple[Market, MarketSnapshot]:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t4-{key}-market"))
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t4-{key}-snapshot"))
    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t4-{key}-source"))
    market = MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"pyt188-t4-{key}",
            question="Will this resolve YES?",
            category="politics",
            resolution_rule="UMA optimistic oracle",
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
            liquidity=5000.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=source_id, name=f"source-{key}", kind="news", reliability=0.9)
    )
    from pythia.rag import EvidenceStore, StubEmbeddingPort

    EvidenceStore(session, StubEmbeddingPort()).add(
        market_id=market_id,
        source_id=source_id,
        observed_at=ASOF - timedelta(days=1),
        content="Some evidence about the market.",
    )
    return market, snapshot


def _pipeline(session: Session, *, source: SignalSource) -> Pipeline:
    return Pipeline(
        session=session,
        llm=StubLLMPort(),
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=_settings(),
        routing=ROUTING,
        source=source,
    )


def test_pipeline_stamps_smartmoney_on_estimate_and_score(session: Session) -> None:
    market, snapshot = _seed(session, key="sm")
    result = _pipeline(session, source=SignalSource.SMARTMONEY).run(market, snapshot)

    assert result.estimate is not None
    assert result.score is not None
    assert result.estimate.signal_source is SignalSource.SMARTMONEY

    persisted_estimate = EstimateRepository(session).get(result.estimate.id)
    assert persisted_estimate is not None
    assert persisted_estimate.signal_source is SignalSource.SMARTMONEY

    persisted_score = ScoreRepository(session).get_by_estimate(result.estimate.id)
    assert persisted_score is not None
    assert persisted_score.signal_source is SignalSource.SMARTMONEY


def test_pipeline_stamps_radar_on_estimate_and_score(session: Session) -> None:
    market, snapshot = _seed(session, key="radar")
    result = _pipeline(session, source=SignalSource.RADAR).run(market, snapshot)

    assert result.estimate is not None
    assert result.score is not None
    assert result.estimate.signal_source is SignalSource.RADAR

    persisted_estimate = EstimateRepository(session).get(result.estimate.id)
    assert persisted_estimate is not None
    assert persisted_estimate.signal_source is SignalSource.RADAR

    persisted_score = ScoreRepository(session).get_by_estimate(result.estimate.id)
    assert persisted_score is not None
    assert persisted_score.signal_source is SignalSource.RADAR
