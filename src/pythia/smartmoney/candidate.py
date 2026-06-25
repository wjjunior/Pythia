"""Candidate enqueue & evaluation (v2 §5.2, §4.6).

New increases are grouped into **one candidate per market** with merged trader attribution, run
through the *existing* v1 pipeline up to ``CANDIDATE_DAILY_CAP`` per day (rest queued), idempotent
per ``(market, asof)``. ``PipelineCandidateEvaluator`` resolves the Falcon market to a tracked v1
market and runs the pipeline at its latest snapshot — the verdict flows to the paper ledger exactly
as a crawler-sourced market (§5.2). A market we do not yet track is skipped; the crawler ingests it.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlmodel import Session

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    TrackedTrader,
    TraderEntry,
)
from pythia.common.ids import deterministic_id
from pythia.common.logging import get_logger
from pythia.db.repositories import MarketRepository, SnapshotRepository
from pythia.db.smartmoney import TraderEntryRepo
from pythia.ingestion.falcon import FalconPort
from pythia.orchestration import Pipeline
from pythia.smartmoney.detector import detect_new_entries

_log = get_logger(__name__)


class CandidateEvaluator(Protocol):
    def evaluate(self, external_market: str, asof: datetime, trader_ids: list[str]) -> str | None:
        """Run the v1 pipeline for the market at asof; return our market_id, or None if not run."""
        ...


class CandidateStore(Protocol):
    def get(self, signal_id: str) -> CandidateSignal | None: ...
    def enqueue(self, signal: CandidateSignal) -> CandidateSignal: ...


def enqueue_candidates(
    entries: Sequence[TraderEntry],
    *,
    store: CandidateStore,
    evaluator: CandidateEvaluator,
    daily_cap: int,
    evaluated_today: int,
    created_at: datetime,
) -> list[CandidateSignal]:
    """One candidate per market (merged trader_ids, §5.2); evaluate up to the daily cap; persist."""
    by_market: dict[str, list[TraderEntry]] = defaultdict(list)
    for entry in entries:
        by_market[entry.external_market].append(entry)

    budget = max(0, daily_cap - evaluated_today)
    signals: list[CandidateSignal] = []
    for external_market in sorted(by_market):
        group = by_market[external_market]
        asof = max(entry.ts for entry in group)
        trader_ids = sorted({entry.trader_id for entry in group})
        signal_id = deterministic_id(external_market, asof.isoformat())
        if store.get(signal_id) is not None:
            continue  # already enqueued this (market, asof) — idempotent, no re-evaluation

        market_id: str | None = None
        status = CandidateSignalStatus.QUEUED  # over the daily cap → deferred
        if budget > 0:
            market_id = evaluator.evaluate(external_market, asof, trader_ids)
            status = CandidateSignalStatus.EVALUATED if market_id else CandidateSignalStatus.SKIPPED
            if market_id is not None:
                budget -= 1
        signals.append(
            store.enqueue(
                CandidateSignal(
                    id=signal_id,
                    market_id=market_id,
                    asof=asof,
                    trader_ids=trader_ids,
                    source=CandidateSource.NEW_ENTRY,
                    status=status,
                    created_at=created_at,
                )
            )
        )
    return signals


class PipelineCandidateEvaluator:
    """Resolve a Falcon market to a tracked v1 market; run the pipeline at its latest snapshot."""

    def __init__(self, session: Session, pipeline: Pipeline) -> None:
        self._markets = MarketRepository(session)
        self._snapshots = SnapshotRepository(session)
        self._pipeline = pipeline

    def evaluate(self, external_market: str, asof: datetime, trader_ids: list[str]) -> str | None:
        market = self._markets.get_by_external_id(external_market)
        if market is None:
            _log.info("candidate_skipped_untracked_market", external_market=external_market)
            return None
        snapshot = self._snapshots.latest_for_market(market.id)
        if snapshot is None:
            _log.info("candidate_skipped_no_snapshot", market_id=market.id)
            return None
        _log.info(
            "candidate_evaluated",
            market_id=market.id,
            asof=asof.isoformat(),
            trader_count=len(trader_ids),
        )
        self._pipeline.run(
            market, snapshot
        )  # verdict persisted, as a crawler-sourced market (§5.2)
        return market.id


def detect_and_enqueue(
    traders: Sequence[TrackedTrader],
    *,
    falcon: FalconPort,
    since: datetime,
    entry_repo: TraderEntryRepo,
    store: CandidateStore,
    evaluator: CandidateEvaluator,
    daily_cap: int,
    evaluated_today: int,
    now: datetime,
) -> list[CandidateSignal]:
    """Detect each active wallet's new entries, then enqueue deduped candidates (one tick of §5)."""
    triggers: list[TraderEntry] = []
    for trader in traders:
        triggers.extend(
            detect_new_entries(trader, falcon.get_trader_trades(trader.wallet, since), entry_repo)
        )
    return enqueue_candidates(
        triggers,
        store=store,
        evaluator=evaluator,
        daily_cap=daily_cap,
        evaluated_today=evaluated_today,
        created_at=now,
    )
