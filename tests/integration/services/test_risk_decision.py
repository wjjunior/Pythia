"""Integration (PYT-172): risk-approved size per estimate for the radar approve action."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import AuditEvent, AuditKind, Market
from pythia.db.repositories import AuditRepository, MarketRepository
from pythia.services.risk_decision import suggested_size_by_estimate

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _market(session: Session, name: str) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
    MarketRepository(session).upsert(
        Market(
            id=market_id, external_id=f"0x{name}", question="q", category="c", resolution_rule="r"
        )
    )
    return market_id


def _risk(
    session: Session,
    market_id: str,
    estimate_id: str | None,
    *,
    approved_size: float,
    vetoed: bool,
    ts: datetime,
) -> None:
    AuditRepository(session).append(
        AuditEvent(
            kind=AuditKind.RISK_DECISION,
            market_id=market_id,
            payload={"estimate_id": estimate_id, "approved_size": approved_size, "vetoed": vetoed},
            ts=ts,
        )
    )


def test_suggested_size_returns_latest_approved_size_per_estimate(session: Session) -> None:
    approved_m = _market(session, "ss-approved")
    vetoed_m = _market(session, "ss-vetoed")
    untouched_m = _market(session, "ss-untouched")
    approved_e = str(uuid.uuid5(uuid.NAMESPACE_URL, "ss-approved-e"))
    vetoed_e = str(uuid.uuid5(uuid.NAMESPACE_URL, "ss-vetoed-e"))
    untouched_e = str(uuid.uuid5(uuid.NAMESPACE_URL, "ss-untouched-e"))
    _risk(session, approved_m, approved_e, approved_size=12.5, vetoed=False, ts=_NOW)
    _risk(session, vetoed_m, vetoed_e, approved_size=0.0, vetoed=True, ts=_NOW)

    sizes = suggested_size_by_estimate(
        session,
        [(approved_m, approved_e), (vetoed_m, vetoed_e), (untouched_m, untouched_e)],
    )

    assert sizes[approved_e] == 12.5
    assert sizes[vetoed_e] == 0.0  # vetoed ⇒ approved_size 0 ⇒ not approvable
    assert sizes[untouched_e] == 0.0  # no risk decision ⇒ not approvable


def test_latest_risk_decision_wins_even_when_it_vetoes(session: Session) -> None:
    market_id = _market(session, "ss-reeval")
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "ss-reeval-e"))
    _risk(session, market_id, estimate_id, approved_size=10.0, vetoed=False, ts=_NOW)
    _risk(
        session,
        market_id,
        estimate_id,
        approved_size=0.0,
        vetoed=True,
        ts=_NOW + timedelta(minutes=5),
    )

    sizes = suggested_size_by_estimate(session, [(market_id, estimate_id)])

    assert sizes[estimate_id] == 0.0  # the later veto governs, not the earlier approval


def test_suggested_size_is_empty_for_no_pairs(session: Session) -> None:
    assert suggested_size_by_estimate(session, []) == {}
