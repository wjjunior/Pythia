"""PYT-188 T5c: every arb leg trade is stamped with SignalSource.ARB."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import Market, MarketSnapshot, SignalSource
from pythia.config import Settings
from pythia.db.repositories import ArbRepository, MarketRepository, SnapshotRepository
from pythia.orchestration.runner import run_event_pass
from pythia.paper import PaperLedger
from pythia.services.arbs import ArbApprovalService

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_YES = 0.3  # three legs at 0.3 → asks sum 0.9 < 1 → dutch book
_BASKETS = 5.0


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _market_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5c-{name}"))


def _seed_option(session: Session, name: str) -> None:
    MarketRepository(session).upsert(
        Market(
            id=_market_id(name),
            external_id=f"0xpyt188-t5c-{name}",
            question=f"Will {name} win?",
            category="politics",
            resolution_rule="r",
            event_slug="pyt188-t5c-cup",
            neg_risk=True,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5c-snap-{name}")),
            market_id=_market_id(name),
            ts=_NOW,
            yes_price=_YES,
            spread=0.0,
            volume=1.0,
            liquidity=2.0,  # raised from 1.0: must exceed size = _BASKETS * _YES = 1.5
        )
    )


def _detect_arb(session: Session) -> str:
    for name in ("a", "b", "c"):
        _seed_option(session, name)
    assert run_event_pass(session, _settings(), now=_NOW) == 1
    arb = ArbRepository(session).list_ranked()[0]
    assert arb.id is not None
    return arb.id


def _service(session: Session) -> ArbApprovalService:
    return ArbApprovalService(session, PaperLedger(session), _settings())


def test_arb_approval_stamps_arb_on_every_leg_trade(session: Session) -> None:
    arb_id = _detect_arb(session)
    trades = _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)

    assert len(trades) == 3
    assert all(t.signal_source is SignalSource.ARB for t in trades), (
        f"leg sources: {[t.signal_source for t in trades]}"
    )
