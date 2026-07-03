"""signal_source CHECK constraints + candidate_signals.estimate_id FK (PYT-188 Task 2)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pythia.db import models

pytestmark = pytest.mark.integration

_TS = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _seed_market(session: Session, tag: str) -> uuid.UUID:
    market_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-{tag}-market")
    session.add(
        models.Market(
            id=market_id,
            external_id=f"pyt188-{tag}",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    session.commit()
    return market_id


def _seed_estimate(session: Session, market_id: uuid.UUID, tag: str) -> uuid.UUID:
    snapshot_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-{tag}-snap")
    session.add(
        models.MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    session.commit()
    estimate_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-{tag}-estimate")
    session.add(
        models.Estimate(
            id=estimate_id,
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=0.6,
            confidence=0.8,
            lower=0.5,
            upper=0.7,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )
    session.commit()
    return estimate_id


# ---- paper_trades.signal_source ----


def test_paper_trade_signal_source_rejects_bogus(session: Session) -> None:
    market_id = _seed_market(session, "pt-bogus")
    estimate_id = _seed_estimate(session, market_id, "pt-bogus")
    session.add(
        models.PaperTrade(
            market_id=market_id,
            estimate_id=estimate_id,
            side="YES",
            entry_price=0.5,
            size=10.0,
            signal_source="bogus",  # not in the CHECK vocabulary
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_paper_trade_signal_source_accepts_radar(session: Session) -> None:
    market_id = _seed_market(session, "pt-radar")
    estimate_id = _seed_estimate(session, market_id, "pt-radar")
    trade = models.PaperTrade(
        market_id=market_id,
        estimate_id=estimate_id,
        side="YES",
        entry_price=0.5,
        size=10.0,
        signal_source="radar",
    )
    session.add(trade)
    session.commit()
    fetched = session.get(models.PaperTrade, trade.id)
    assert fetched is not None
    assert fetched.signal_source == "radar"


def test_paper_trade_signal_source_accepts_null(session: Session) -> None:
    market_id = _seed_market(session, "pt-null")
    estimate_id = _seed_estimate(session, market_id, "pt-null")
    trade = models.PaperTrade(
        market_id=market_id,
        estimate_id=estimate_id,
        side="NO",
        entry_price=0.4,
        size=5.0,
    )
    session.add(trade)
    session.commit()
    fetched = session.get(models.PaperTrade, trade.id)
    assert fetched is not None
    assert fetched.signal_source is None


# ---- candidate_signals.estimate_id FK ----


def test_candidate_signal_estimate_id_accepts_valid_estimate(session: Session) -> None:
    market_id = _seed_market(session, "cs-fk-ok")
    estimate_id = _seed_estimate(session, market_id, "cs-fk-ok")
    signal = models.CandidateSignal(
        market_id=market_id,
        asof=_TS,
        trader_ids=[],
        source="new_entry",
        status="queued",
        estimate_id=estimate_id,
    )
    session.add(signal)
    session.commit()
    fetched = session.get(models.CandidateSignal, signal.id)
    assert fetched is not None
    assert fetched.estimate_id == estimate_id


def test_candidate_signal_estimate_id_rejects_nonexistent_fk(session: Session) -> None:
    market_id = _seed_market(session, "cs-fk-bad")
    phantom_id = uuid.uuid4()
    signal = models.CandidateSignal(
        market_id=market_id,
        asof=_TS,
        trader_ids=[],
        source="new_entry",
        status="queued",
        estimate_id=phantom_id,  # no row in estimates
    )
    session.add(signal)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_candidate_signal_estimate_id_accepts_null(session: Session) -> None:
    market_id = _seed_market(session, "cs-null")
    signal = models.CandidateSignal(
        market_id=market_id,
        asof=_TS,
        trader_ids=[],
        source="new_entry",
        status="queued",
    )
    session.add(signal)
    session.commit()
    fetched = session.get(models.CandidateSignal, signal.id)
    assert fetched is not None
    assert fetched.estimate_id is None
