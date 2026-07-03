"""Integration tests — idempotent signal_source backfill (PYT-188 Task 7).

Seed real rows, call backfill_signal_source twice, assert labels and idempotency.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    Market,
    MarketSnapshot,
    SignalSource,
    TrackedTrader,
    TraderState,
)
from pythia.db import models
from pythia.db.repositories import (
    MarketRepository,
    SnapshotRepository,
)
from pythia.db.smartmoney import CandidateSignalRepo, TrackedTraderRepo
from pythia.worker.backfill_signal_source import backfill_signal_source

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_EVAL_WINDOW = timedelta(hours=1)

# ── helpers ──────────────────────────────────────────────────────────────────


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _market(session: Session, name: str) -> Market:
    return MarketRepository(session).upsert(
        Market(
            id=_uid(name),
            external_id=name,
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )


def _snapshot(session: Session, market: Market, *, ts: datetime = _NOW) -> MarketSnapshot:
    return SnapshotRepository(session).append(
        MarketSnapshot(
            id=_uid(f"{market.external_id}-snap"),
            market_id=market.id,
            ts=ts,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )


def _estimate(
    session: Session,
    market: Market,
    snap: MarketSnapshot,
    *,
    name: str,
    model_name: str = "stub",
    signal_source: SignalSource | None = None,
    created_at: datetime = _NOW,
) -> models.Estimate:
    """Seed an estimate with signal_source NULL (historical) or a given value."""
    row = models.Estimate(
        id=uuid.UUID(_uid(name)),
        market_id=uuid.UUID(market.id),
        snapshot_id=uuid.UUID(snap.id),
        p_model=0.6,
        confidence=0.8,
        lower=0.5,
        upper=0.7,
        rationale="r",
        evidence_ids=[],
        model_name=model_name,
        signal_source=signal_source.value if signal_source is not None else None,
        created_at=created_at,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _score(session: Session, estimate_row: models.Estimate, *, signal_source: str | None) -> None:
    row = models.Score(
        estimate_id=estimate_row.id,
        edge=0.1,
        edge_net=0.05,
        ev_unit=0.05,
        p_resol=0.9,
        p_liq=0.9,
        p_time=0.9,
        p_corr=1.0,
        opportunity_score=0.5,
        kelly_fraction=0.1,
        signal_source=signal_source,
    )
    session.add(row)
    session.commit()


def _trade(
    session: Session,
    market: Market,
    *,
    name: str,
    estimate_row: models.Estimate | None = None,
    signal_source: str | None = None,
) -> models.PaperTrade:
    row = models.PaperTrade(
        id=uuid.UUID(_uid(name)),
        market_id=uuid.UUID(market.id),
        estimate_id=estimate_row.id if estimate_row is not None else None,
        side="YES",
        entry_price=0.5,
        size=10.0,
        status="open",
        signal_source=signal_source,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _candidate(
    session: Session,
    market: Market,
    *,
    name: str,
    asof: datetime,
    status: CandidateSignalStatus = CandidateSignalStatus.EVALUATED,
    estimate_row: models.Estimate | None = None,
) -> None:
    trader = TrackedTraderRepo(session).add(
        TrackedTrader(
            id=_uid(f"trader-{name}"),
            wallet=f"0x{name}",
            state=TraderState.ACTIVE,
            added_at=_NOW,
            state_since=_NOW,
        )
    )
    signal_id = _uid(f"sig-{name}")
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=signal_id,
            market_id=market.id,
            asof=asof,
            trader_ids=[trader.id],
            source=CandidateSource.NEW_ENTRY,
            status=status,
            created_at=asof,
        )
    )
    if estimate_row is not None:
        CandidateSignalRepo(session).set_estimate(signal_id, str(estimate_row.id))


# ── tests ─────────────────────────────────────────────────────────────────────


def test_radar_estimate_no_candidate(session: Session) -> None:
    """No candidate_signals on this market → label is `radar`."""
    mkt = _market(session, "bfs-radar")
    snap = _snapshot(session, mkt)
    est = _estimate(session, mkt, snap, name="est-radar")

    counts = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)

    assert counts["estimates"] == 1
    session.refresh(est)
    assert est.signal_source == SignalSource.RADAR.value


def test_smartmoney_estimate_within_candidate_window(session: Session) -> None:
    """Estimate created inside [candidate.asof, candidate.asof + eval_window] → `smartmoney`."""
    mkt = _market(session, "bfs-sm")
    snap = _snapshot(session, mkt)
    candidate_asof = _NOW - timedelta(minutes=30)
    # estimate created 15 min after candidate.asof → inside 1h window
    est_created = candidate_asof + timedelta(minutes=15)
    est = _estimate(session, mkt, snap, name="est-sm", created_at=est_created)
    _candidate(session, mkt, name="sm", asof=candidate_asof, estimate_row=est)

    counts = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)

    assert counts["estimates"] == 1
    session.refresh(est)
    assert est.signal_source == SignalSource.SMARTMONEY.value


def test_unknown_estimate_outside_candidate_window(session: Session) -> None:
    """Candidate exists but estimate created outside the window → `unknown`."""
    mkt = _market(session, "bfs-unk")
    snap = _snapshot(session, mkt)
    candidate_asof = _NOW - timedelta(hours=3)
    # estimate created 2h AFTER candidate.asof + 1h window → outside
    est_created = candidate_asof + timedelta(hours=2)
    est = _estimate(session, mkt, snap, name="est-unk", created_at=est_created)
    _candidate(session, mkt, name="unk", asof=candidate_asof)

    counts = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)

    assert counts["estimates"] == 1
    session.refresh(est)
    assert est.signal_source == SignalSource.UNKNOWN.value


def test_event_coupled_inherits_parent_smartmoney(session: Session) -> None:
    """event-coupled estimate whose non-coupled parent is `smartmoney` → inherits `smartmoney`."""
    mkt = _market(session, "bfs-coupled")
    snap = _snapshot(session, mkt)
    candidate_asof = _NOW - timedelta(minutes=30)
    est_created = candidate_asof + timedelta(minutes=10)
    # non-coupled parent
    parent_est = _estimate(session, mkt, snap, name="est-coupled-parent", created_at=est_created)
    _candidate(session, mkt, name="coupled", asof=candidate_asof, estimate_row=parent_est)
    # event-coupled estimate
    coupled_est = _estimate(
        session,
        mkt,
        snap,
        name="est-coupled-child",
        model_name="event-coupled",
        created_at=est_created + timedelta(seconds=1),
    )

    counts = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)

    assert counts["estimates"] >= 2
    session.refresh(parent_est)
    assert parent_est.signal_source == SignalSource.SMARTMONEY.value
    session.refresh(coupled_est)
    assert coupled_est.signal_source == SignalSource.SMARTMONEY.value


def test_score_inherits_estimate_source(session: Session) -> None:
    """Score gets the 1:1 estimate's source via the join."""
    mkt = _market(session, "bfs-score")
    snap = _snapshot(session, mkt)
    est = _estimate(session, mkt, snap, name="est-score")
    _score(session, est, signal_source=None)

    counts = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)

    assert counts["scores"] == 1
    score_row = session.exec(
        __import__("sqlmodel", fromlist=["select"])
        .select(models.Score)
        .where(models.Score.estimate_id == est.id)
    ).one()
    assert score_row.signal_source == SignalSource.RADAR.value  # no candidate → radar


def test_arb_trade_no_estimate(session: Session) -> None:
    """Paper trade with estimate_id IS NULL → `arb`."""
    mkt = _market(session, "bfs-arb")
    trade = _trade(session, mkt, name="trade-arb", estimate_row=None)

    counts = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)

    assert counts["paper_trades"] == 1
    session.refresh(trade)
    assert trade.signal_source == SignalSource.ARB.value


def test_review_trade_inherits_estimate_source(session: Session) -> None:
    """Paper trade with estimate_id → copies estimate's source (radar here)."""
    mkt = _market(session, "bfs-review")
    snap = _snapshot(session, mkt)
    est = _estimate(session, mkt, snap, name="est-review")
    trade = _trade(session, mkt, name="trade-review", estimate_row=est)

    counts = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)

    assert counts["paper_trades"] == 1
    session.refresh(trade)
    assert trade.signal_source == SignalSource.RADAR.value  # estimate has no candidate → radar


def test_idempotency_second_run_changes_nothing(session: Session) -> None:
    """A second run with everything already labelled returns all-zero counts."""
    mkt = _market(session, "bfs-idem")
    snap = _snapshot(session, mkt)
    est = _estimate(session, mkt, snap, name="est-idem")
    _score(session, est, signal_source=None)
    _trade(session, mkt, name="trade-idem", estimate_row=est)

    first = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)
    assert sum(first.values()) > 0  # something was labelled in the first run

    # Record what got labelled
    session.refresh(est)
    label_after_first = est.signal_source

    second = backfill_signal_source(session, now=_NOW, eval_window=_EVAL_WINDOW)
    assert second == {"estimates": 0, "scores": 0, "paper_trades": 0}

    session.refresh(est)
    assert est.signal_source == label_after_first  # label unchanged
