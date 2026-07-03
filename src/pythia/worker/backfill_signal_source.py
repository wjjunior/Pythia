"""Idempotent one-off backfill: label historical signal_source IS NULL rows (PYT-188 Task 7).

Backfill order: estimates → scores → paper_trades (later steps read earlier results).
Every UPDATE is scoped WHERE signal_source IS NULL — a second run flips nothing.

Estimates labelling rules (producer set is exhaustive, exclusion is sound):
  1. model_name='event-coupled': inherit the market's latest non-coupled estimate's already-computed
     source. Non-coupled estimates are processed first so the source is available.
  2. No `evaluated` candidate_signals row on this market → `radar` (only remaining producer).
  3. An `evaluated` candidate on that market has candidate.asof ≤ estimate.created_at
     ≤ candidate.asof + eval_window → `smartmoney`.
  4. Otherwise → `unknown` (candidate activity but the window join can't decide).

Scores: copy the 1:1 estimate's source (join on scores.estimate_id = estimates.id).
Paper trades: estimate_id IS NULL → 'arb'; else copy the estimate's source.
"""

import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import Session, col, select

from pythia.common import SignalSource
from pythia.common.logging import get_logger
from pythia.db import models

_log = get_logger(__name__)

_EVENT_COUPLED = "event-coupled"
_EVALUATED = "evaluated"


def backfill_signal_source(
    session: Session,
    *,
    now: datetime,
    eval_window: timedelta = timedelta(hours=1),
) -> dict[str, int]:
    """Fill signal_source IS NULL rows on estimates, scores, and paper_trades.

    Returns per-table counts of rows labelled. Idempotent: a second run returns all zeros
    because every UPDATE is scoped WHERE signal_source IS NULL.
    """
    estimates_count = _backfill_estimates(session, eval_window=eval_window)
    scores_count = _backfill_scores(session)
    trades_count = _backfill_paper_trades(session)
    _log.info(
        "backfill_signal_source",
        estimates=estimates_count,
        scores=scores_count,
        paper_trades=trades_count,
    )
    return {"estimates": estimates_count, "scores": scores_count, "paper_trades": trades_count}


def _backfill_estimates(session: Session, *, eval_window: timedelta) -> int:
    """Label estimates with signal_source IS NULL.

    Non-coupled estimates are processed before coupled ones so that the coupled
    inherit already-computed sources from their non-coupled parents.
    """
    non_coupled_count = _label_non_coupled_estimates(session, eval_window=eval_window)
    coupled_count = _label_coupled_estimates(session)
    return non_coupled_count + coupled_count


def _label_non_coupled_estimates(session: Session, *, eval_window: timedelta) -> int:
    """Label non-coupled estimates (model_name != 'event-coupled') WHERE signal_source IS NULL."""
    # Fetch non-coupled, NULL-source estimates: id, market_id, created_at
    rows = session.exec(
        select(models.Estimate)
        .where(col(models.Estimate.signal_source).is_(None))
        .where(col(models.Estimate.model_name) != _EVENT_COUPLED)
    ).all()
    if not rows:
        return 0

    market_ids = [r.market_id for r in rows if r.market_id is not None]

    # Fetch evaluated candidates for those markets once (avoid N+1)
    candidate_rows = session.exec(
        select(models.CandidateSignal)
        .where(col(models.CandidateSignal.status) == _EVALUATED)
        .where(col(models.CandidateSignal.market_id).in_(market_ids))
    ).all()

    markets_with_candidate: set[uuid.UUID] = {
        r.market_id for r in candidate_rows if r.market_id is not None
    }

    candidates_by_market: dict[uuid.UUID, list[datetime]] = defaultdict(list)
    for r in candidate_rows:
        if r.market_id is not None:
            candidates_by_market[r.market_id].append(r.asof)

    radar_ids: list[uuid.UUID] = []
    smartmoney_ids: list[uuid.UUID] = []
    unknown_ids: list[uuid.UUID] = []

    for row in rows:
        est_id = row.id
        market_id = row.market_id
        created_at = row.created_at
        if est_id is None or created_at is None:
            continue
        if market_id not in markets_with_candidate:
            radar_ids.append(est_id)
        else:
            window_hit = any(
                asof <= created_at <= asof + eval_window for asof in candidates_by_market[market_id]
            )
            if window_hit:
                smartmoney_ids.append(est_id)
            else:
                unknown_ids.append(est_id)

    count = 0
    if radar_ids:
        session.execute(
            update(models.Estimate)
            .where(col(models.Estimate.id).in_(radar_ids))
            .where(col(models.Estimate.signal_source).is_(None))
            .values(signal_source=SignalSource.RADAR.value)
        )
        count += len(radar_ids)
    if smartmoney_ids:
        session.execute(
            update(models.Estimate)
            .where(col(models.Estimate.id).in_(smartmoney_ids))
            .where(col(models.Estimate.signal_source).is_(None))
            .values(signal_source=SignalSource.SMARTMONEY.value)
        )
        count += len(smartmoney_ids)
    if unknown_ids:
        session.execute(
            update(models.Estimate)
            .where(col(models.Estimate.id).in_(unknown_ids))
            .where(col(models.Estimate.signal_source).is_(None))
            .values(signal_source=SignalSource.UNKNOWN.value)
        )
        count += len(unknown_ids)

    if count:
        session.commit()
    return count


def _label_coupled_estimates(session: Session) -> int:
    """For event-coupled estimates with signal_source IS NULL, inherit the latest non-coupled
    estimate's (already-labelled) source for the same market.

    Non-coupled estimates are processed first so their signal_source is already set.
    """
    coupled_rows = session.exec(
        select(models.Estimate)
        .where(col(models.Estimate.signal_source).is_(None))
        .where(col(models.Estimate.model_name) == _EVENT_COUPLED)
    ).all()

    if not coupled_rows:
        return 0

    market_ids = [r.market_id for r in coupled_rows if r.market_id is not None]

    # Fetch non-coupled, labelled estimates ordered by created_at desc, id desc
    non_coupled = session.exec(
        select(models.Estimate)
        .where(col(models.Estimate.market_id).in_(market_ids))
        .where(col(models.Estimate.model_name) != _EVENT_COUPLED)
        .where(col(models.Estimate.signal_source).is_not(None))
        .order_by(
            col(models.Estimate.market_id),
            col(models.Estimate.created_at).desc(),
            col(models.Estimate.id).desc(),
        )
    ).all()

    # Latest non-coupled source per market (first row per market after ordering)
    latest_source_by_market: dict[uuid.UUID, str] = {}
    for r in non_coupled:
        mid, src = r.market_id, r.signal_source
        if mid is not None and src is not None and mid not in latest_source_by_market:
            latest_source_by_market[mid] = src

    by_source: dict[str, list[uuid.UUID]] = {}
    for row in coupled_rows:
        if row.id is None or row.market_id is None:
            continue
        src = latest_source_by_market.get(row.market_id)
        if src is None:
            continue  # no labelled non-coupled parent yet — skip
        by_source.setdefault(src, []).append(row.id)

    count = 0
    for src, ids in by_source.items():
        session.execute(
            update(models.Estimate)
            .where(col(models.Estimate.id).in_(ids))
            .where(col(models.Estimate.signal_source).is_(None))
            .values(signal_source=src)
        )
        count += len(ids)

    if count:
        session.commit()
    return count


def _backfill_scores(session: Session) -> int:
    """Copy signal_source from estimates to their 1:1 scores WHERE scores.signal_source IS NULL."""
    scores = session.exec(
        select(models.Score).where(col(models.Score.signal_source).is_(None))
    ).all()

    if not scores:
        return 0

    estimate_ids = [s.estimate_id for s in scores]
    estimates = session.exec(
        select(models.Estimate)
        .where(col(models.Estimate.id).in_(estimate_ids))
        .where(col(models.Estimate.signal_source).is_not(None))
    ).all()

    source_by_estimate_id: dict[uuid.UUID, str] = {
        e.id: e.signal_source for e in estimates if e.id is not None and e.signal_source is not None
    }

    by_source: dict[str, list[uuid.UUID]] = {}
    for score in scores:
        src = source_by_estimate_id.get(score.estimate_id)
        if src is None:
            continue
        by_source.setdefault(src, []).append(score.estimate_id)

    count = 0
    for src, eids in by_source.items():
        session.execute(
            update(models.Score)
            .where(col(models.Score.estimate_id).in_(eids))
            .where(col(models.Score.signal_source).is_(None))
            .values(signal_source=src)
        )
        count += len(eids)

    if count:
        session.commit()
    return count


def _backfill_paper_trades(session: Session) -> int:
    """Label paper_trades WHERE signal_source IS NULL:
    - estimate_id IS NULL → 'arb'
    - else copy estimate's signal_source
    """
    trades = session.exec(
        select(models.PaperTrade).where(col(models.PaperTrade.signal_source).is_(None))
    ).all()

    if not trades:
        return 0

    arb_ids: list[uuid.UUID] = [t.id for t in trades if t.id is not None and t.estimate_id is None]
    review_trades = [t for t in trades if t.estimate_id is not None]

    # Fetch estimate sources for review trades
    review_estimate_ids = [t.estimate_id for t in review_trades]
    estimates = session.exec(
        select(models.Estimate)
        .where(col(models.Estimate.id).in_(review_estimate_ids))
        .where(col(models.Estimate.signal_source).is_not(None))
    ).all()

    source_by_estimate_id: dict[uuid.UUID, str] = {
        e.id: e.signal_source for e in estimates if e.id is not None and e.signal_source is not None
    }

    by_source: dict[str, list[uuid.UUID]] = {}
    for trade in review_trades:
        if trade.id is None or trade.estimate_id is None:
            continue
        src = source_by_estimate_id.get(trade.estimate_id)
        if src is None:
            continue
        by_source.setdefault(src, []).append(trade.id)

    count = 0
    if arb_ids:
        session.execute(
            update(models.PaperTrade)
            .where(col(models.PaperTrade.id).in_(arb_ids))
            .where(col(models.PaperTrade.signal_source).is_(None))
            .values(signal_source=SignalSource.ARB.value)
        )
        count += len(arb_ids)

    for src, trade_ids in by_source.items():
        session.execute(
            update(models.PaperTrade)
            .where(col(models.PaperTrade.id).in_(trade_ids))
            .where(col(models.PaperTrade.signal_source).is_(None))
            .values(signal_source=src)
        )
        count += len(trade_ids)

    if count:
        session.commit()
    return count
