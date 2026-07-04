"""PYT-189 T6: veto an arb basket when any leg exceeds its market's approval-time liquidity."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import Market, MarketSnapshot
from pythia.config import Settings
from pythia.db.repositories import (
    ArbRepository,
    MarketRepository,
    PaperTradeRepository,
    SnapshotRepository,
)
from pythia.orchestration.runner import run_event_pass
from pythia.paper import PaperLedger
from pythia.services.arbs import ArbApprovalService, LiquidityExceededError

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_YES = 0.3  # three exclusive options at 0.3 ⇒ dutch book, ev = 0.1
_BASKETS = 10.0  # size per leg = 10.0 * 0.3 = 3.0


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _market_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt189-t6-{name}"))


def _seed_option(session: Session, name: str, liquidity: float) -> None:
    MarketRepository(session).upsert(
        Market(
            id=_market_id(name),
            external_id=f"0xpyt189-t6-{name}",
            question=f"Will {name} win?",
            category="politics",
            resolution_rule="r",
            event_slug="pyt189-t6-cup",
            neg_risk=True,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt189-t6-snap-{name}")),
            market_id=_market_id(name),
            ts=_NOW,
            yes_price=_YES,
            spread=0.0,
            volume=1.0,
            liquidity=liquidity,
        )
    )


def _detect_arb(session: Session, liquidity_by_name: dict[str, float]) -> str:
    for name in ("a", "b", "c"):
        _seed_option(session, name, liquidity=liquidity_by_name[name])
    assert run_event_pass(session, _settings(), now=_NOW) == 1
    arb = ArbRepository(session).list_ranked()[0]
    assert arb.id is not None
    return arb.id


def _service(session: Session) -> ArbApprovalService:
    return ArbApprovalService(session, PaperLedger(session), _settings())


def test_veto_when_one_leg_exceeds_liquidity(session: Session) -> None:
    """Basket is vetoed (all-or-none) when one leg's size > its snapshot liquidity."""
    # size = _BASKETS * _YES = 10 * 0.3 = 3.0
    # leg "b" has liquidity=2.0 < 3.0 → the whole basket must be rejected
    arb_id = _detect_arb(session, {"a": 5.0, "b": 2.0, "c": 5.0})

    with pytest.raises(LiquidityExceededError):
        _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)

    # all-or-none: no paper_trades rows created
    assert PaperTradeRepository(session).list_open() == []


def test_approve_succeeds_when_all_legs_within_liquidity(session: Session) -> None:
    """Basket opens normally when every leg has sufficient liquidity."""
    # size = 10 * 0.3 = 3.0; all legs have liquidity=5.0 > 3.0
    arb_id = _detect_arb(session, {"a": 5.0, "b": 5.0, "c": 5.0})

    trades = _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)

    assert len(trades) == 3
    assert len(PaperTradeRepository(session).list_open()) == 3
