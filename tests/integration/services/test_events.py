"""Integration test (PYT-137): EventService groups option-markets + classifies, point-in-time."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import EventType, Market, MarketSnapshot
from pythia.db.repositories import MarketRepository, SnapshotRepository
from pythia.services.events import EventService

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _seed_option(
    session: Session, event_slug: str, title: str, *, neg_risk: bool, yes_price: float
) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_slug}-{title}"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"0x{event_slug}-{title}",
            question=f"Will {title} win?",
            category="sports",
            resolution_rule="r",
            event_slug=event_slug,
            neg_risk=neg_risk,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-{market_id}")),
            market_id=market_id,
            ts=_NOW,
            yes_price=yes_price,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return market_id


def test_event_service_groups_and_classifies_a_negrisk_event(session: Session) -> None:
    a = _seed_option(session, "cup", "Brazil", neg_risk=True, yes_price=0.40)
    b = _seed_option(session, "cup", "Argentina", neg_risk=True, yes_price=0.35)
    _seed_option(session, "other", "X", neg_risk=True, yes_price=0.50)  # different event — excluded

    group = EventService(session).get_event_group("cup", _NOW)

    assert group is not None
    assert group.event_type is EventType.MUTUALLY_EXCLUSIVE
    assert {o.market_id for o in group.options} == {a, b}
    assert sorted(o.yes_price for o in group.options) == pytest.approx([0.35, 0.40])


def test_event_service_returns_none_for_an_unknown_event(session: Session) -> None:
    assert EventService(session).get_event_group("nope", _NOW) is None
