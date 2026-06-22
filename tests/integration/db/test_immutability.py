"""Storage-layer proof that market_snapshots and evidence reject in-place UPDATE (AC-2)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

from pythia.db import models

pytestmark = pytest.mark.integration

_ORIGINAL_PRICE = 0.4
_ORIGINAL_CONTENT = "original evidence"
_RAISE_SQLSTATE = "P0001"


def _market(market_id: uuid.UUID) -> models.Market:
    return models.Market(
        id=market_id,
        external_id="ext-immutable",
        question="Will it rain?",
        category="weather",
        resolution_rule="rule",
    )


def _source(source_id: uuid.UUID) -> models.Source:
    return models.Source(id=source_id, name="reuters-immutable", kind="news", reliability=0.8)


def _snapshot(snapshot_id: uuid.UUID, market_id: uuid.UUID) -> models.MarketSnapshot:
    return models.MarketSnapshot(
        id=snapshot_id,
        market_id=market_id,
        ts=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        yes_price=_ORIGINAL_PRICE,
        spread=0.01,
        volume=100.0,
        liquidity=50.0,
    )


def _evidence(
    evidence_id: uuid.UUID, market_id: uuid.UUID, source_id: uuid.UUID
) -> models.Evidence:
    return models.Evidence(
        id=evidence_id,
        market_id=market_id,
        source_id=source_id,
        observed_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        content=_ORIGINAL_CONTENT,
    )


def test_market_snapshots_should_reject_in_place_update(session: Session) -> None:
    market_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    session.add(_market(market_id))
    session.commit()
    session.add(_snapshot(snapshot_id, market_id))
    session.commit()

    with pytest.raises(DBAPIError) as exc_info:
        session.connection().execute(
            text("UPDATE market_snapshots SET yes_price = :p WHERE id = :id"),
            {"p": 0.99, "id": snapshot_id},
        )
    assert getattr(exc_info.value.orig, "sqlstate", None) == _RAISE_SQLSTATE

    session.rollback()
    snapshot = session.get(models.MarketSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.yes_price == _ORIGINAL_PRICE


def test_evidence_should_reject_in_place_update(session: Session) -> None:
    market_id = uuid.uuid4()
    source_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    session.add(_market(market_id))
    session.add(_source(source_id))
    session.commit()
    session.add(_evidence(evidence_id, market_id, source_id))
    session.commit()

    with pytest.raises(DBAPIError) as exc_info:
        session.connection().execute(
            text("UPDATE evidence SET content = :c WHERE id = :id"),
            {"c": "tampered", "id": evidence_id},
        )
    assert getattr(exc_info.value.orig, "sqlstate", None) == _RAISE_SQLSTATE

    session.rollback()
    evidence = session.get(models.Evidence, evidence_id)
    assert evidence is not None
    assert evidence.content == _ORIGINAL_CONTENT


def test_market_snapshots_should_reject_orm_attribute_mutation(session: Session) -> None:
    market_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    session.add(_market(market_id))
    session.commit()
    snapshot = _snapshot(snapshot_id, market_id)
    session.add(snapshot)
    session.commit()

    snapshot.yes_price = 0.99
    session.add(snapshot)
    with pytest.raises(DBAPIError) as exc_info:
        session.commit()
    assert getattr(exc_info.value.orig, "sqlstate", None) == _RAISE_SQLSTATE

    session.rollback()
    session.refresh(snapshot)
    assert snapshot.yes_price == _ORIGINAL_PRICE
