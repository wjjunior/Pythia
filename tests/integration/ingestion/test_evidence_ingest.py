"""Evidence ingest tick on real pgvector (§2.1, PYT-40): fixture → store, point-in-time."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select

from pythia.common import Market, MarketSnapshot
from pythia.db import models
from pythia.db.repositories import MarketRepository, SnapshotRepository, SourceRepository
from pythia.ingestion.evidence import (
    EvidenceArticle,
    EvidenceRateLimitedError,
    EvidenceTransport,
    FixtureEvidenceTransport,
    newsapi,
)
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


def _seed_market(session: Session, key: str, *, close_time: datetime | None = None) -> Market:
    return MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"ev-{key}")),
            external_id=f"ev-{key}",
            question=f"Will event {key} resolve YES?",
            category="politics",
            resolution_rule="r",
            close_time=close_time,
        )
    )


def _seed_snapshot(session: Session, market_id: str, *, yes_price: float, ts: datetime) -> None:
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-{market_id}")),
            market_id=market_id,
            ts=ts,
            yes_price=yes_price,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )


def _markets_with_evidence(session: Session) -> set[str]:
    rows = session.exec(select(models.Evidence.market_id).distinct()).all()
    return {str(m) for m in rows}


def test_ingest_tick_excludes_closed_markets(session: Session) -> None:
    open_market = _seed_market(session, "open", close_time=T1 + timedelta(hours=1))
    _seed_market(session, "closed", close_time=T1 - timedelta(hours=1))  # trading already ended
    ingest_tick(session, StubEmbeddingPort(), [_transport()], ts=T1)
    assert _markets_with_evidence(session) == {open_market.id}  # the closed market is skipped


def test_ingest_tick_prioritizes_midband_over_tail(session: Session) -> None:
    midband = _seed_market(session, "mid")
    tail = _seed_market(session, "tail")
    _seed_snapshot(session, midband.id, yes_price=0.5, ts=T1)  # inside 0.1..0.85 → estimated
    _seed_snapshot(session, tail.id, yes_price=0.02, ts=T1)  # extreme longshot → deprioritized
    ingest_tick(
        session, StubEmbeddingPort(), [_transport()], ts=T1, max_markets=1, midband=(0.1, 0.85)
    )
    assert _markets_with_evidence(session) == {midband.id}  # scarce budget lands on the candidate


def test_ingest_tick_skips_markets_with_recent_evidence(session: Session) -> None:
    _market(session)
    embedder = StubEmbeddingPort()
    first = ingest_tick(session, embedder, [_transport()], ts=T1, refresh_hours=24)
    # An hour later the market already has fresh evidence (< 24h) → not re-fetched (PYT-173).
    second = ingest_tick(session, embedder, [_transport()], ts=T1 + timedelta(hours=1))
    assert first >= 1
    assert second == 0


def test_ingest_tick_refetches_once_evidence_is_stale(session: Session) -> None:
    market = _market(session)
    embedder = StubEmbeddingPort()
    ingest_tick(session, embedder, [_transport()], ts=T1, refresh_hours=24)
    # 25h later the evidence is stale → the market is a target again.
    count = ingest_tick(session, embedder, [_transport()], ts=T1 + timedelta(hours=25))
    assert count >= 1
    later = EvidenceStore(session, embedder).retrieve(
        market.id, asof=T1 + timedelta(hours=25), k=10
    )
    assert len(later) >= 2  # both the original and the refreshed evidence are retained (immutable)


def test_ingest_tick_respects_the_per_tick_cap(session: Session) -> None:
    for key in ("a", "b", "c"):
        _seed_market(session, key)
    ingest_tick(session, StubEmbeddingPort(), [_transport()], ts=T1, max_markets=2)
    assert len(_markets_with_evidence(session)) == 2  # only the cap's worth were fetched


class _RateLimitedTransport:
    """A transport that always 429s — exercises the circuit-break."""

    name = "ratelimited"
    reliability = 0.6

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, query: str) -> list[dict[str, Any]]:
        self.calls += 1
        raise EvidenceRateLimitedError("ratelimited rate-limited (429)")

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return None


def test_ingest_tick_circuit_breaks_on_rate_limit(session: Session) -> None:
    for key in ("a", "b", "c"):
        _seed_market(session, key)
    transport = _RateLimitedTransport()
    total = ingest_tick(session, StubEmbeddingPort(), [transport], ts=T1)
    assert total == 0
    assert transport.calls == 1  # the source stops after the first 429, not once per market


def test_ingest_tick_rotates_one_source_per_market(session: Session) -> None:
    for key in ("a", "b"):
        _seed_market(session, key)
    two_sources: list[EvidenceTransport] = [
        FixtureEvidenceTransport(
            FIXTURES / "newsapi_results.json",
            to_article=newsapi.to_article,
            extract=lambda data: data["articles"],
            source_name=name,
            source_reliability=0.6,
        )
        for name in ("newsapi", "newsdata")
    ]
    ingest_tick(session, StubEmbeddingPort(), two_sources, ts=T1)
    used = session.exec(select(models.Evidence.source_id).distinct()).all()
    assert len(used) == 2  # each market drew a different source (round-robin), not both from one
