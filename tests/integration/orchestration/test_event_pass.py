"""Integration (PYT-141): the event pass detects + persists structural arbitrage."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import Market, MarketSnapshot
from pythia.db.repositories import ArbRepository, MarketRepository, SnapshotRepository
from pythia.orchestration.runner import run_event_pass

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _seed_option(
    session: Session, event_slug: str, name: str, *, yes_price: float, resolved: bool = False
) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_slug}-{name}"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"0x{event_slug}-{name}",
            question=f"Will {name} win?",
            category="politics",
            resolution_rule="r",
            event_slug=event_slug,
            neg_risk=True,
            resolved=resolved,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-{market_id}")),
            market_id=market_id,
            ts=_NOW,
            yes_price=yes_price,
            spread=0.0,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return market_id


def test_event_pass_detects_and_persists_a_dutch_book(session: Session) -> None:
    # Three mutually-exclusive options whose asks (spread 0) sum to 0.9 < 1 → a locked dutch book.
    for name in ("a", "b", "c"):
        _seed_option(session, "cup", name, yes_price=0.3)

    count = run_event_pass(session, now=_NOW)

    assert count == 1
    arbs = ArbRepository(session).list_ranked()
    assert len(arbs) == 1
    assert arbs[0].ev == pytest.approx(0.1)  # 1 - 0.9
    assert len(arbs[0].legs) == 3


def test_event_pass_persists_nothing_for_a_consistently_priced_event(session: Session) -> None:
    _seed_option(session, "cup", "a", yes_price=0.6)
    _seed_option(session, "cup", "b", yes_price=0.5)  # asks sum 1.1 >= 1 → no arb
    assert run_event_pass(session, now=_NOW) == 0
    assert ArbRepository(session).list_ranked() == []


def test_event_pass_excludes_resolved_options(session: Session) -> None:
    # The two open options form a dutch book (asks 0.4 + 0.4 < 1); a resolved option must not enter
    # the group — pulling it in (yes 1.0) would push the sum >= 1 and hide the real arb.
    _seed_option(session, "cup", "a", yes_price=0.4)
    _seed_option(session, "cup", "b", yes_price=0.4)
    _seed_option(session, "cup", "done", yes_price=1.0, resolved=True)
    assert run_event_pass(session, now=_NOW) == 1
    assert len(ArbRepository(session).list_ranked()[0].legs) == 2  # only the open options
