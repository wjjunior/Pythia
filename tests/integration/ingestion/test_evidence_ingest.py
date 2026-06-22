"""Evidence ingest tick on real pgvector (§2.1, PYT-40): fixture → store, point-in-time."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session

from pythia.common import Market
from pythia.db.repositories import MarketRepository, SourceRepository
from pythia.ingestion.evidence import EvidenceTransport, FixtureEvidenceTransport, newsapi
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.worker.evidence_ingest import ingest_tick

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "evidence"
T1 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
T0 = T1 - timedelta(hours=1)


def _market(session: Session) -> Market:
    return MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "evidence-ingest-market")),
            external_id="evidence-ingest-ext",
            question="Will candidate Alpha win the 2026 governor election?",
            category="politics",
            resolution_rule="r",
        )
    )


def _transport() -> EvidenceTransport:
    return FixtureEvidenceTransport(
        FIXTURES / "newsapi_results.json",
        to_article=newsapi.to_article,
        extract=lambda data: data["articles"],
        source_name="newsapi",
        source_reliability=0.6,
    )


def test_ingest_tick_persists_evidence_for_open_market(session: Session) -> None:
    market = _market(session)
    transport = _transport()
    embedder = StubEmbeddingPort()
    count = ingest_tick(session, embedder, [transport], ts=T1)
    assert count >= 1
    evidence = EvidenceStore(session, embedder).retrieve(market.id, asof=T1, k=10)
    assert len(evidence) >= 1
    assert all(e.observed_at == T1 for e in evidence)


def test_ingest_tick_returns_zero_for_no_transports(session: Session) -> None:
    _market(session)
    assert ingest_tick(session, StubEmbeddingPort(), [], ts=T1) == 0


def test_ingest_tick_is_idempotent_at_same_timestamp(session: Session) -> None:
    _market(session)
    transport = _transport()
    embedder = StubEmbeddingPort()
    first = ingest_tick(session, embedder, [transport], ts=T1)
    second = ingest_tick(session, embedder, [transport], ts=T1)
    assert first >= 1
    assert second == 0


def test_point_in_time_invariant_holds(session: Session) -> None:
    market = _market(session)
    ingest_tick(session, StubEmbeddingPort(), [_transport()], ts=T1)
    before = EvidenceStore(session, StubEmbeddingPort()).retrieve(market.id, asof=T0, k=10)
    assert before == []


def test_source_is_registered_on_ingest(session: Session) -> None:
    _market(session)
    ingest_tick(session, StubEmbeddingPort(), [_transport()], ts=T1)
    source = SourceRepository(session).get_by_name("newsapi")
    assert source is not None
    assert source.kind == "news"
    assert source.reliability == pytest.approx(0.6)
