"""Integration (PYT-145): approve a detected arb basket — market-anchored fills, locked-EV PnL."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import Market, MarketSnapshot, Side, TradeStatus
from pythia.config import Settings
from pythia.db.repositories import (
    ArbRepository,
    MarketRepository,
    PaperTradeRepository,
    SnapshotRepository,
)
from pythia.orchestration.runner import run_event_pass
from pythia.paper import PaperLedger, fill_price
from pythia.services.arbs import (
    ArbAlreadyApprovedError,
    ArbApprovalService,
    UnknownArbError,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_SETTINGS = Settings(_env_file=None, database_url="postgresql+psycopg://x/y")
_YES = 0.3  # three exclusive options at 0.3 ⇒ asks sum 0.9 < 1 ⇒ dutch book, ev = 0.1
_BASKETS = 10.0


def _market_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cup-{name}"))


def _seed_option(session: Session, name: str) -> None:
    MarketRepository(session).upsert(
        Market(
            id=_market_id(name),
            external_id=f"0xcup-{name}",
            question=f"Will {name} win?",
            category="politics",
            resolution_rule="r",
            event_slug="cup",
            neg_risk=True,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-cup-{name}")),
            market_id=_market_id(name),
            ts=_NOW,
            yes_price=_YES,
            spread=0.0,
            volume=1.0,
            liquidity=1.0,
        )
    )


def _detect_arb(session: Session) -> str:
    for name in ("a", "b", "c"):
        _seed_option(session, name)
    assert run_event_pass(session, now=_NOW) == 1  # one dutch book detected + persisted
    arb = ArbRepository(session).list_ranked()[0]
    assert arb.id is not None
    return arb.id


def _service(session: Session) -> ArbApprovalService:
    return ArbApprovalService(session, PaperLedger(session), _SETTINGS)


def test_approve_opens_all_legs_at_the_observed_snapshot_price(session: Session) -> None:
    arb_id = _detect_arb(session)
    assert PaperTradeRepository(session).list_open() == []  # nothing opens until approval

    trades = _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)

    assert len(trades) == 3
    assert all(t.status is TradeStatus.OPEN for t in trades)
    assert all(t.estimate_id is None for t in trades)  # market-anchored, no model forecast
    expected_entry = fill_price(_YES, Side.YES, 0.0, fee=0.0, slippage=0.0)
    assert all(t.entry_price == pytest.approx(expected_entry) for t in trades)
    assert all(t.size == pytest.approx(_BASKETS * _YES) for t in trades)  # baskets shares per leg


def test_approve_unknown_arb_raises(session: Session) -> None:
    with pytest.raises(UnknownArbError):
        _service(session).approve(str(uuid.uuid4()), baskets=_BASKETS, at=_NOW)


def test_approve_rejects_non_positive_baskets(session: Session) -> None:
    arb_id = _detect_arb(session)
    with pytest.raises(ValueError, match="positive"):
        _service(session).approve(arb_id, baskets=0.0, at=_NOW)


def test_re_approving_the_same_arb_raises_already_approved(session: Session) -> None:
    arb_id = _detect_arb(session)
    _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)
    with pytest.raises(ArbAlreadyApprovedError):  # deterministic leg ids collide on re-approval
        _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)


def test_resolved_basket_realizes_the_locked_ev(session: Session) -> None:
    arb_id = _detect_arb(session)
    arb = ArbRepository(session).get(arb_id)
    assert arb is not None
    _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)

    ledger = PaperLedger(session)
    ledger.resolve(_market_id("a"), outcome=1, at=_NOW)  # exactly one option resolves YES
    ledger.resolve(_market_id("b"), outcome=0, at=_NOW)
    ledger.resolve(_market_id("c"), outcome=0, at=_NOW)

    realized = PaperTradeRepository(session).realized_pnl()
    assert realized == pytest.approx(_BASKETS * arb.ev)  # locked EV: 10 * 0.1 = 1.0
