"""Schema-level invariants on real Postgres (§4.7, PYT-44/PYT-46)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, col

from pythia.common import CandidateStatus
from pythia.db import models

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _seed_postmortem(session: Session) -> uuid.UUID:
    market_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt44-constraint-market")
    session.add(
        models.Market(
            id=market_id,
            external_id="pyt44-constraint",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    session.commit()
    postmortem_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt44-constraint-postmortem")
    session.add(
        models.Postmortem(
            id=postmortem_id,
            market_id=market_id,
            category="politics",
            failure_class="model_overconfidence",
            brier=0.5,
            rationale="r",
            evidence_ids=[],
        )
    )
    session.commit()
    return postmortem_id


def _candidate(postmortem_id: uuid.UUID, status: str) -> models.ImprovementCandidate:
    return models.ImprovementCandidate(
        postmortem_id=postmortem_id,
        kind="prompt",
        status=status,
        rationale="r",
        requires_review=False,
    )


def test_improvement_candidate_status_rejects_unknown_value(session: Session) -> None:
    postmortem_id = _seed_postmortem(session)
    session.add(_candidate(postmortem_id, status="bogus"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_improvement_candidate_status_accepts_every_candidate_status(session: Session) -> None:
    postmortem_id = _seed_postmortem(session)
    for status in CandidateStatus:
        session.add(_candidate(postmortem_id, status=status.value))
        session.commit()


def _seed_snapshot(session: Session) -> uuid.UUID:
    market_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt46-snap-market")
    session.add(
        models.Market(
            id=market_id,
            external_id="pyt46-snap",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    session.commit()
    snapshot_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt46-snapshot")
    session.add(
        models.MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    session.commit()
    return snapshot_id


def _seed_evidence(session: Session) -> uuid.UUID:
    market_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt46-ev-market")
    source_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt46-ev-source")
    session.add(
        models.Market(
            id=market_id,
            external_id="pyt46-ev",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    session.add(models.Source(id=source_id, name="pyt46-reuters", kind="news", reliability=0.9))
    session.commit()
    evidence_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt46-evidence")
    session.add(
        models.Evidence(
            id=evidence_id,
            market_id=market_id,
            source_id=source_id,
            observed_at=_TS,
            content="original",
        )
    )
    session.commit()
    return evidence_id


def test_market_snapshots_reject_in_place_update(session: Session) -> None:
    snapshot_id = _seed_snapshot(session)
    with pytest.raises(DBAPIError):
        session.execute(
            update(models.MarketSnapshot)
            .where(col(models.MarketSnapshot.id) == snapshot_id)
            .values(yes_price=0.99)
        )
    session.rollback()
    row = session.get(models.MarketSnapshot, snapshot_id)
    assert row is not None and row.yes_price == 0.5


def test_evidence_rejects_in_place_update(session: Session) -> None:
    evidence_id = _seed_evidence(session)
    with pytest.raises(DBAPIError):
        session.execute(
            update(models.Evidence)
            .where(col(models.Evidence.id) == evidence_id)
            .values(content="tampered")
        )
    session.rollback()
    row = session.get(models.Evidence, evidence_id)
    assert row is not None and row.content == "original"


def _seed_resolution(session: Session) -> uuid.UUID:
    market_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt102-res-market")
    session.add(
        models.Market(
            id=market_id,
            external_id="pyt102-res",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    session.commit()
    resolution_id = uuid.uuid5(uuid.NAMESPACE_URL, "pyt102-resolution")
    session.add(
        models.Resolution(
            id=resolution_id,
            market_id=market_id,
            outcome=1,
            resolved_at=_TS,
            ambiguous=False,
        )
    )
    session.commit()
    return resolution_id


def test_should_reject_resolutions_in_place_update(session: Session) -> None:
    resolution_id = _seed_resolution(session)
    with pytest.raises(DBAPIError):
        session.execute(
            update(models.Resolution)
            .where(col(models.Resolution.id) == resolution_id)
            .values(resolved_at=_TS + timedelta(days=99))
        )
    session.rollback()
    row = session.get(models.Resolution, resolution_id)
    assert row is not None and row.resolved_at == _TS
