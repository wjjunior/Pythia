"""Per-category invalid/dispute-rate queries against real Postgres (§4.4.2, PYT-35)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.db import models
from pythia.db.repositories import ResolutionRepository

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


def _resolved(
    session: Session, name: str, category: str, *, ambiguous: bool, resolved_at: datetime
) -> None:
    session.add(
        models.Market(
            id=_uid(name), external_id=name, question="q", category=category, resolution_rule="r"
        )
    )
    session.commit()
    session.add(
        models.Resolution(
            market_id=_uid(name), outcome=0, resolved_at=resolved_at, ambiguous=ambiguous
        )
    )
    session.commit()


def test_invalid_counts_by_category_counts_only_ambiguous(session: Session) -> None:
    _resolved(session, "p1", "politics", ambiguous=False, resolved_at=_TS)
    _resolved(session, "p2", "politics", ambiguous=True, resolved_at=_TS)
    _resolved(session, "s1", "sports", ambiguous=True, resolved_at=_TS)
    assert ResolutionRepository(session).invalid_counts_by_category() == {
        "politics": 1,
        "sports": 1,
    }


def test_dispute_counts_are_windowed_point_in_time(session: Session) -> None:
    _resolved(session, "p1", "politics", ambiguous=False, resolved_at=_TS - timedelta(days=5))
    _resolved(session, "p2", "politics", ambiguous=True, resolved_at=_TS - timedelta(days=5))
    _resolved(session, "old", "politics", ambiguous=True, resolved_at=_TS - timedelta(days=100))
    invalid, total = ResolutionRepository(session).dispute_counts(
        "politics", since=_TS - timedelta(days=90), asof=_TS
    )
    assert (invalid, total) == (1, 2)  # the 100-day-old dispute is outside the window
