"""signal_source hydration: NULL→UNKNOWN mapping + persist on write (PYT-188 Task 3)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session

from pythia.common import (
    Estimate,
    Market,
    MarketSnapshot,
    PaperTrade,
    Score,
    Side,
    SignalSource,
    TradeStatus,
)
from pythia.db import models
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    PaperTradeRepository,
    ScoreRepository,
    SnapshotRepository,
)
from pythia.paper.ledger import PaperLedger

pytestmark = pytest.mark.integration

_TS = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _seed_market(session: Session, tag: str) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t3-{tag}-market"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"pyt188-t3-{tag}",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    return market_id


def _seed_snapshot(session: Session, market_id: str, tag: str) -> str:
    snap_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t3-{tag}-snap"))
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snap_id,
            market_id=market_id,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return snap_id


def _seed_estimate(
    session: Session, market_id: str, snap_id: str, tag: str, source: SignalSource
) -> str:
    est_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t3-{tag}-est"))
    EstimateRepository(session).add(
        Estimate(
            id=est_id,
            market_id=market_id,
            snapshot_id=snap_id,
            p_model=0.6,
            confidence=0.8,
            lower=0.5,
            upper=0.7,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=source,
        )
    )
    return est_id


# ---- NULL → UNKNOWN hydration ----


def test_estimate_null_signal_source_hydrates_to_unknown(session: Session) -> None:
    """A row inserted with signal_source IS NULL must hydrate to SignalSource.UNKNOWN."""
    market_id = _seed_market(session, "est-null")
    snap_id = _seed_snapshot(session, market_id, "est-null")
    est_id = _seed_estimate(session, market_id, snap_id, "est-null", SignalSource.UNKNOWN)

    # Force NULL to simulate a legacy/migrated row that pre-dates the column
    session.exec(  # type: ignore[call-overload]
        text("UPDATE estimates SET signal_source = NULL WHERE id = :id"),
        params={"id": uuid.UUID(est_id)},
    )
    session.commit()

    fetched = EstimateRepository(session).get(est_id)
    assert fetched is not None
    assert fetched.signal_source is SignalSource.UNKNOWN


def test_estimate_radar_signal_source_round_trips(session: Session) -> None:
    """Persisting an Estimate with signal_source=RADAR writes 'radar' to the DB column."""
    market_id = _seed_market(session, "est-radar")
    snap_id = _seed_snapshot(session, market_id, "est-radar")
    est_id = _seed_estimate(session, market_id, snap_id, "est-radar", SignalSource.RADAR)

    # Assert the raw DB column holds the string 'radar'
    raw = session.exec(  # type: ignore[call-overload]
        text("SELECT signal_source FROM estimates WHERE id = :id"),
        params={"id": uuid.UUID(est_id)},
    ).one_or_none()
    assert raw is not None
    assert raw[0] == "radar"

    # Assert the domain layer reflects it correctly on hydration
    fetched = EstimateRepository(session).get(est_id)
    assert fetched is not None
    assert fetched.signal_source is SignalSource.RADAR


def test_score_null_signal_source_hydrates_to_unknown(session: Session) -> None:
    """A score row inserted with signal_source IS NULL must hydrate to SignalSource.UNKNOWN."""
    market_id = _seed_market(session, "score-null")
    snap_id = _seed_snapshot(session, market_id, "score-null")
    est_id = _seed_estimate(session, market_id, snap_id, "score-null", SignalSource.UNKNOWN)

    ScoreRepository(session).add(
        Score(
            estimate_id=est_id,
            edge=0.1,
            edge_net=0.08,
            ev_unit=0.08,
            p_resol=0.9,
            p_liq=0.95,
            p_time=0.9,
            p_corr=1.0,
            opportunity_score=0.5,
            kelly_fraction=0.05,
            signal_source=SignalSource.UNKNOWN,
        )
    )
    # Force NULL to simulate a legacy row
    session.exec(  # type: ignore[call-overload]
        text("UPDATE scores SET signal_source = NULL WHERE estimate_id = :id"),
        params={"id": uuid.UUID(est_id)},
    )
    session.commit()

    fetched = ScoreRepository(session).get_by_estimate(est_id)
    assert fetched is not None
    assert fetched.signal_source is SignalSource.UNKNOWN


def test_score_radar_signal_source_round_trips(session: Session) -> None:
    """Persisting a Score with signal_source=RADAR writes 'radar' to the DB column."""
    market_id = _seed_market(session, "score-radar")
    snap_id = _seed_snapshot(session, market_id, "score-radar")
    est_id = _seed_estimate(session, market_id, snap_id, "score-radar", SignalSource.RADAR)

    ScoreRepository(session).add(
        Score(
            estimate_id=est_id,
            edge=0.1,
            edge_net=0.08,
            ev_unit=0.08,
            p_resol=0.9,
            p_liq=0.95,
            p_time=0.9,
            p_corr=1.0,
            opportunity_score=0.5,
            kelly_fraction=0.05,
            signal_source=SignalSource.RADAR,
        )
    )

    # Assert the raw DB column holds the string 'radar'
    raw = session.exec(  # type: ignore[call-overload]
        text("SELECT signal_source FROM scores WHERE estimate_id = :id"),
        params={"id": uuid.UUID(est_id)},
    ).one_or_none()
    assert raw is not None
    assert raw[0] == "radar"

    fetched = ScoreRepository(session).get_by_estimate(est_id)
    assert fetched is not None
    assert fetched.signal_source is SignalSource.RADAR


def test_paper_trade_null_signal_source_hydrates_to_unknown(session: Session) -> None:
    """A paper_trade row with signal_source IS NULL hydrates to SignalSource.UNKNOWN."""
    market_id = _seed_market(session, "trade-null")
    snap_id = _seed_snapshot(session, market_id, "trade-null")
    est_id = _seed_estimate(session, market_id, snap_id, "trade-null", SignalSource.UNKNOWN)

    trade_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt188-t3-trade-null-trade"))
    ledger = PaperLedger(session)
    trade = ledger.open(
        PaperTrade(
            id=trade_id,
            market_id=market_id,
            estimate_id=est_id,
            side=Side.YES,
            entry_price=0.5,
            size=10.0,
            status=TradeStatus.OPEN,
            opened_at=_TS,
            signal_source=SignalSource.UNKNOWN,
        )
    )
    # Force NULL to simulate a legacy row
    session.exec(  # type: ignore[call-overload]
        text("UPDATE paper_trades SET signal_source = NULL WHERE id = :id"),
        params={"id": uuid.UUID(trade.id)},
    )
    session.commit()
    session.expire_all()

    # Confirm NULL is in the DB
    row = session.get(models.PaperTrade, uuid.UUID(trade.id))
    assert row is not None
    assert row.signal_source is None

    fetched_list = PaperTradeRepository(session).list_all()
    fetched = next(t for t in fetched_list if t.id == trade.id)
    assert fetched.signal_source is SignalSource.UNKNOWN
