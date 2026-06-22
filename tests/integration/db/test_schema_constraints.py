"""Schema-level invariants on real Postgres (§4.7, PYT-44)."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pythia.common import CandidateStatus
from pythia.db import models

pytestmark = pytest.mark.integration


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
