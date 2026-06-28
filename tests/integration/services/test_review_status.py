"""Integration (PYT-153): per-market review status from paper trades + review-gate rejections."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import AuditEvent, AuditKind, Market, ReviewStatus, TradeStatus
from pythia.db import models
from pythia.db.repositories import AuditRepository, MarketRepository
from pythia.services.review_status import review_status_by_market

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


def _trade(session: Session, market_id: str, status: TradeStatus) -> None:
    session.add(
        models.PaperTrade(
            id=uuid.uuid4(),
            market_id=uuid.UUID(market_id),
            estimate_id=None,
            side="YES",
            entry_price=0.5,
            size=1.0,
            status=status.value,
            opened_at=_NOW,
        )
    )
    session.commit()


def test_review_status_reflects_paper_trades_and_rejections(session: Session) -> None:
    none = _market(session, "rs-none")
    opened = _market(session, "rs-open")
    resolved = _market(session, "rs-resolved")
    rejected = _market(session, "rs-rejected")
    _trade(session, opened, TradeStatus.OPEN)
    _trade(session, resolved, TradeStatus.RESOLVED)
    AuditRepository(session).append(
        AuditEvent(kind=AuditKind.REJECTION, market_id=rejected, payload={})
    )

    status = review_status_by_market(session, [none, opened, resolved, rejected])

    assert status[none] is ReviewStatus.NONE
    assert status[opened] is ReviewStatus.OPEN
    assert status[resolved] is ReviewStatus.RESOLVED
    assert status[rejected] is ReviewStatus.REJECTED


def test_open_position_dominates_a_rejection(session: Session) -> None:
    market_id = _market(session, "rs-both")
    AuditRepository(session).append(
        AuditEvent(kind=AuditKind.REJECTION, market_id=market_id, payload={})
    )
    _trade(session, market_id, TradeStatus.OPEN)

    assert review_status_by_market(session, [market_id])[market_id] is ReviewStatus.OPEN
