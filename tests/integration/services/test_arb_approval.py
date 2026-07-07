"""Integration (PYT-145): approve a detected arb basket — market-anchored fills, locked-EV PnL."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import Market, MarketSnapshot, Side, TradeStatus
from pythia.config import Settings
from pythia.db import models
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
    ArbStaleError,
    ArbUnprofitableError,
    UnknownArbError,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_SETTINGS = Settings(_env_file=None, database_url="postgresql+psycopg://x/y")
_YES = 0.3  # three exclusive options at 0.3 ⇒ asks sum 0.9 < 1 ⇒ dutch book, ev = 0.1
_BASKETS = 10.0


def _market_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cup-{name}"))


def _cup_uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"cup-{name}")


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
            liquidity=5.0,  # raised from 1.0: must exceed size = _BASKETS * _YES = 3.0
        )
    )


def _detect_arb(session: Session) -> str:
    for name in ("a", "b", "c"):
        _seed_option(session, name)
    assert run_event_pass(session, _SETTINGS, now=_NOW) == 1  # one dutch book detected + persisted
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


def test_re_running_the_event_pass_keeps_identity_and_blocks_re_approval(session: Session) -> None:
    arb_id = _detect_arb(session)
    _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)
    opened = len(PaperTradeRepository(session).list_open())
    assert opened == 3

    later = _NOW + timedelta(minutes=45)
    assert run_event_pass(session, _SETTINGS, now=later) == 1  # same economic arb, upserted
    same = ArbRepository(session).list_ranked()[0]
    assert same.id == arb_id  # identity preserved across the pass

    with pytest.raises(ArbAlreadyApprovedError):
        _service(session).approve(arb_id, baskets=_BASKETS, at=later)
    assert len(PaperTradeRepository(session).list_open()) == opened  # basket NOT doubled


def test_persistence_lifetime_advances_last_seen_but_fixes_first_detected(session: Session) -> None:
    arb_id = _detect_arb(session)
    first = ArbRepository(session).get(arb_id)
    assert first is not None and first.first_detected_at == _NOW and first.last_seen_at == _NOW

    later = _NOW + timedelta(minutes=45)
    run_event_pass(session, _SETTINGS, now=later)
    again = ArbRepository(session).get(arb_id)
    assert again is not None
    assert again.first_detected_at == _NOW  # fixed
    assert again.last_seen_at == later  # advanced


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


def test_approve_refuses_a_resolved_leg_as_stale(session: Session) -> None:
    arb_id = _detect_arb(session)
    market = session.get(models.Market, _cup_uuid("a"))
    assert market is not None
    market.resolved = True
    session.add(market)
    session.commit()
    with pytest.raises(ArbStaleError):
        _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW)
    assert PaperTradeRepository(session).list_open() == []


def test_approve_refuses_when_fresh_prices_make_net_ev_non_positive(session: Session) -> None:
    arb_id = _detect_arb(session)  # detected at yes=0.3 → gross ev 0.10
    for name in ("a", "b", "c"):  # prices moved up: asks now sum > 1 → net ev < 0
        SnapshotRepository(session).append(
            MarketSnapshot(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap2-cup-{name}")),
                market_id=_market_id(name),
                ts=_NOW + timedelta(minutes=5),
                yes_price=0.40,
                spread=0.0,
                volume=1.0,
                liquidity=50.0,
            )
        )
    with pytest.raises(ArbUnprofitableError):
        _service(session).approve(arb_id, baskets=_BASKETS, at=_NOW + timedelta(minutes=5))
    assert PaperTradeRepository(session).list_open() == []
