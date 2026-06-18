"""End-to-end estimation against real pgvector: point-in-time citation + persistence (§5).

Wires the estimation agent to the real EvidenceStore (PYT-17) so the anti-lookahead guarantee is
proven through the actual SQL retrieval, not a fake, then persists the estimate and reads it back.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.agents import EstimationAgent, StubLLMPort
from pythia.common import Market, MarketSnapshot, Source
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.rag import EvidenceStore, StubEmbeddingPort

pytestmark = pytest.mark.integration

QUESTION = "Will candidate Alpha win the 2026 governor election?"
MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt18-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt18-snapshot"))
SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt18-source"))
ASOF = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _seed(session: Session) -> tuple[Market, MarketSnapshot, set[str], set[str]]:
    market = MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt18",
            question=QUESTION,
            category="politics",
            resolution_rule="Certified result of the 2026 governor election.",
        )
    )
    snapshot = SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=ASOF,
            yes_price=0.6,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_ID, name="reuters", kind="news", reliability=0.9)
    )
    store = EvidenceStore(session, StubEmbeddingPort())
    past = store.add(
        market_id=MARKET_ID,
        source_id=SOURCE_ID,
        observed_at=ASOF - timedelta(days=1),
        content="Polls show candidate Alpha leading the 2026 governor election.",
    )
    future = store.add(
        market_id=MARKET_ID,
        source_id=SOURCE_ID,
        observed_at=ASOF + timedelta(days=1),
        content="Candidate Alpha has won the 2026 governor election (future leak).",
    )
    return market, snapshot, {e.id for e in past}, {e.id for e in future}


def test_estimate_cites_only_point_in_time_evidence(session: Session) -> None:
    market, snapshot, past_ids, future_ids = _seed(session)
    store = EvidenceStore(session, StubEmbeddingPort())
    estimate = EstimationAgent(StubLLMPort(), store).estimate(market, snapshot)
    assert estimate.evidence_ids
    assert set(estimate.evidence_ids) <= past_ids
    assert not set(estimate.evidence_ids) & future_ids


def test_estimate_round_trips_through_the_repository(session: Session) -> None:
    market, snapshot, _, _ = _seed(session)
    store = EvidenceStore(session, StubEmbeddingPort())
    estimate = EstimationAgent(StubLLMPort(), store).estimate(market, snapshot)
    repository = EstimateRepository(session)
    persisted = repository.add(estimate)
    assert repository.get(estimate.id) == persisted
    assert persisted.evidence_ids == estimate.evidence_ids
    assert persisted.model_name == "stub"
