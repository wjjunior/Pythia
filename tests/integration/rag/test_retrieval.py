"""Point-in-time retrieval against a real pgvector database (§4.5).

Proves the headline guarantee: `retrieve` never surfaces evidence observed after `asof`, for any
`asof` (Hypothesis), and that the surviving candidates are ranked by relevance, reliability and
freshness — all on the zero-secret stub embedder.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlmodel import Session

from pythia.common import Market, Source
from pythia.db.repositories import MarketRepository, SourceRepository
from pythia.rag import EvidenceStore, StubEmbeddingPort

pytestmark = pytest.mark.integration

QUESTION = "Will candidate Alpha win the 2026 governor election?"
MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt17-market"))
SOURCE_HIGH_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt17-source-high"))
SOURCE_LOW_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt17-source-low"))

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

SEED: list[tuple[str, str, datetime]] = [
    ("Polls show candidate Alpha leading the 2026 governor election.", SOURCE_HIGH_ID, BASE),
    (
        "Candidate Alpha governor election analysis from a year ago.",
        SOURCE_LOW_ID,
        BASE - timedelta(days=365),
    ),
    (
        "A local bakery introduces a new sourdough bread recipe.",
        SOURCE_LOW_ID,
        BASE - timedelta(days=1),
    ),
    (
        "Early canvassing notes mention candidate Alpha in the governor race.",
        SOURCE_HIGH_ID,
        BASE - timedelta(days=2),
    ),
]


@pytest.fixture
def seeded_store(session: Session) -> EvidenceStore:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt17",
            question=QUESTION,
            category="politics",
            resolution_rule="Certified result of the 2026 governor election.",
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_HIGH_ID, name="reuters", kind="news", reliability=0.9)
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_LOW_ID, name="randomblog", kind="blog", reliability=0.2)
    )
    store = EvidenceStore(session, StubEmbeddingPort())
    for content, source_id, observed_at in SEED:
        store.add(
            market_id=MARKET_ID, source_id=source_id, observed_at=observed_at, content=content
        )
    return store


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(offset_days=st.floats(min_value=-400.0, max_value=5.0, allow_nan=False))
def test_retrieve_never_returns_evidence_observed_after_asof(
    seeded_store: EvidenceStore, offset_days: float
) -> None:
    asof = BASE + timedelta(days=offset_days)
    results = seeded_store.retrieve(MARKET_ID, asof, k=10)
    assert all(evidence.observed_at <= asof for evidence in results)


def test_retrieve_excludes_evidence_newer_than_asof(seeded_store: EvidenceStore) -> None:
    asof = BASE - timedelta(days=3)
    results = seeded_store.retrieve(MARKET_ID, asof, k=10)
    assert {evidence.content for evidence in results} == {
        "Candidate Alpha governor election analysis from a year ago."
    }


def test_retrieve_ranks_relevant_evidence_above_noise(seeded_store: EvidenceStore) -> None:
    results = seeded_store.retrieve(MARKET_ID, BASE + timedelta(hours=1), k=4)
    assert results
    assert "alpha" in results[0].content.lower()
    contents = [evidence.content for evidence in results]
    bakery = "A local bakery introduces a new sourdough bread recipe."
    leading = "Polls show candidate Alpha leading the 2026 governor election."
    assert contents.index(leading) < contents.index(bakery)


def test_retrieve_unknown_market_returns_empty(seeded_store: EvidenceStore) -> None:
    unknown = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt17-missing"))
    assert seeded_store.retrieve(unknown, BASE, k=5) == []
