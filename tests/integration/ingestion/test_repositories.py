"""Integration tests: immutability, idempotency and the source registry (real Postgres)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from pythia.common import Market, MarketSnapshot, Source
from pythia.db import models
from pythia.db.repositories import (
    MarketRepository,
    SnapshotRepository,
    SourceRepository,
    persist_crawl,
)
from pythia.worker.scheduler import make_persist_sink

pytestmark = pytest.mark.integration

_EXTERNAL_ID = "ext-1"
_MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, _EXTERNAL_ID))


def _market(*, resolved: bool = False) -> Market:
    return Market(
        id=_MARKET_ID,
        external_id=_EXTERNAL_ID,
        question="Will it rain?",
        category="weather",
        resolution_rule="rule",
        resolved=resolved,
    )


def _snapshot(ts: datetime, yes_price: float) -> MarketSnapshot:
    return MarketSnapshot(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_EXTERNAL_ID}@{ts.isoformat()}")),
        market_id=_MARKET_ID,
        ts=ts,
        yes_price=yes_price,
        spread=0.01,
        volume=100.0,
        liquidity=50.0,
    )


def test_market_upsert_is_idempotent_and_updates_metadata(session: Session) -> None:
    repo = MarketRepository(session)
    first = repo.upsert(_market(resolved=False))
    second = repo.upsert(_market(resolved=True))
    assert first.id == second.id
    assert second.resolved is True
    assert len(session.exec(select(models.Market)).all()) == 1


def test_snapshots_are_append_only_and_immutable(session: Session) -> None:
    MarketRepository(session).upsert(_market())
    repo = SnapshotRepository(session)
    repo.append(_snapshot(datetime(2026, 6, 18, 12, 0, tzinfo=UTC), 0.4))
    repo.append(_snapshot(datetime(2026, 6, 18, 12, 5, tzinfo=UTC), 0.6))
    snapshots = repo.list_for_market(_MARKET_ID)
    assert [s.yes_price for s in snapshots] == [0.4, 0.6]
    assert not hasattr(repo, "update")


def test_source_registry_persists_reliability(session: Session) -> None:
    repo = SourceRepository(session)
    repo.upsert(Source(id=str(uuid.uuid4()), name="reuters", kind="news", reliability=0.8))
    fetched = repo.get_by_name("reuters")
    assert fetched is not None
    assert fetched.reliability == 0.8


def test_persist_crawl_upserts_market_and_appends_snapshots(session: Session) -> None:
    persist_crawl(session, [(_market(), _snapshot(datetime(2026, 6, 18, 12, 0, tzinfo=UTC), 0.5))])
    persist_crawl(session, [(_market(), _snapshot(datetime(2026, 6, 18, 12, 10, tzinfo=UTC), 0.6))])
    assert len(session.exec(select(models.Market)).all()) == 1
    assert len(SnapshotRepository(session).list_for_market(_MARKET_ID)) == 2


def test_make_persist_sink_writes_the_crawl_to_the_database(session: Session) -> None:
    @contextmanager
    def factory() -> Iterator[Session]:
        yield session

    make_persist_sink(factory)(
        [(_market(), _snapshot(datetime(2026, 6, 18, 12, 0, tzinfo=UTC), 0.5))]
    )

    assert len(session.exec(select(models.Market)).all()) == 1
    assert len(SnapshotRepository(session).list_for_market(_MARKET_ID)) == 1
