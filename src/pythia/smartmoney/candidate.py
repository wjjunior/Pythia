"""Candidate enqueue & evaluation (v2 §5.2, §4.6).

New increases are grouped into **one candidate per market** with merged trader attribution, run
through the *existing* v1 pipeline up to ``CANDIDATE_DAILY_CAP`` per day (rest queued), idempotent
per ``(market, asof)``. ``PipelineCandidateEvaluator`` resolves the Falcon market to a tracked v1
market and runs the pipeline at its latest snapshot — the verdict flows to the paper ledger exactly
as a crawler-sourced market (§5.2). A market we do not yet track is skipped; the crawler ingests it.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
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
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.db.smartmoney import TraderEntryRepo
from pythia.ingestion.falcon import FalconPort
from pythia.ingestion.market_crawler import (
    LiveVenueAccessError,
    TransientTransportError,
    normalize_market,
)
from pythia.orchestration import Pipeline
from pythia.smartmoney.detector import detect_new_entries

_log = get_logger(__name__)


class CandidateEvaluator(Protocol):
    def evaluate(
        self, external_market: str, asof: datetime, trader_ids: list[str]
    ) -> tuple[str, str | None] | None:
        """Run the v1 pipeline for the market; return ``(market_id, estimate_id)`` on success,
        or None if the market could not be resolved/run.  ``estimate_id`` is None only on the
        category-cap veto path (pipeline ran but no estimate). ``asof`` is the trigger time; the
        pipeline runs at the market's latest snapshot, ~the trigger time for new entries."""
        ...

    def resolve(self, external_market: str) -> str | None:
        """Resolve the Falcon market to our tracked market_id *without* running the pipeline, so an
        over-cap (QUEUED) candidate still records the market it triggered (§3.2 guard)."""
        ...

    def run_market(self, market_id: str, asof: datetime, trader_ids: list[str]) -> str | None:
        """Run the v1 pipeline for an already-resolved market.

        Returns the estimate id that was produced or reused (PYT-171 idempotency), or None if the
        market had no snapshot and the pipeline could not run.  A non-None return is truthy, so
        existing ``if evaluator.run_market(...)`` callers stay correct without change.
        """
        ...


class CandidateStore(Protocol):
    def get(self, signal_id: str) -> CandidateSignal | None: ...
    def enqueue(self, signal: CandidateSignal) -> CandidateSignal: ...
    def set_estimate(self, signal_id: str, estimate_id: str) -> None: ...


class QueuedStore(Protocol):
    def list_by_status(self, status: CandidateSignalStatus) -> list[CandidateSignal]: ...
    def set_status(self, signal_id: str, status: CandidateSignalStatus) -> CandidateSignal: ...
    def set_estimate(self, signal_id: str, estimate_id: str) -> None: ...


class CandidateRepo(CandidateStore, QueuedStore, Protocol):
    """A store that both enqueues new candidates and drains the queue."""


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

        estimate_id: str | None = None
        if budget > 0:
            result = evaluator.evaluate(external_market, asof, trader_ids)
            if result is not None:
                market_id, estimate_id = result
                status = CandidateSignalStatus.EVALUATED
                budget -= 1
            else:
                market_id = None
                status = CandidateSignalStatus.SKIPPED
        else:
            # Over the daily cap → deferred. Still resolve the market so the QUEUED trigger blocks
            # double-counting as evidence (§3.2); an untracked market can never run → SKIPPED.
            market_id = evaluator.resolve(external_market)
            status = CandidateSignalStatus.QUEUED if market_id else CandidateSignalStatus.SKIPPED
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
        if estimate_id is not None:
            store.set_estimate(signal_id, estimate_id)
    return signals


class MarketFetcher(Protocol):
    """Fetch a single Polymarket market by condition_id — the live HttpxTransport satisfies it."""

    def fetch_market_by_condition(
        self, condition_id: str, *, include_closed: bool = False
    ) -> dict[str, Any] | None: ...


class PipelineCandidateEvaluator:
    """Resolve a Falcon market to a tracked v1 market; run the pipeline at its latest snapshot."""

    def __init__(
        self,
        session: Session,
        pipeline: Pipeline,
        *,
        market_fetcher: MarketFetcher | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._markets = MarketRepository(session)
        self._snapshots = SnapshotRepository(session)
        self._estimates = EstimateRepository(session)
        self._pipeline = pipeline
        self._fetcher = market_fetcher  # None ⇒ local-only resolve (offline / no live venues)
        self._clock = clock or (lambda: datetime.now(UTC))

    def resolve(self, external_market: str) -> str | None:
        market = self._markets.get_by_external_id(external_market)
        if market is not None:
            return market.id
        ingested = self._ingest_triggered(external_market)  # smart-money discovery (PYT-156)
        if ingested is not None:
            return ingested
        _log.info("candidate_skipped_untracked_market", external_market=external_market)
        return None

    def _ingest_triggered(self, external_market: str) -> str | None:
        # A tracked wallet entered a market outside our crawl: fetch it from Gamma and ingest it so
        # the pipeline can evaluate it, instead of dropping the trigger. Best-effort — any fetch /
        # parse failure degrades to None (SKIPPED), never aborting the detection tick.
        if self._fetcher is None:
            return None
        try:
            raw = self._fetcher.fetch_market_by_condition(external_market)
        except (LiveVenueAccessError, TransientTransportError, httpx.HTTPError, ValueError) as exc:
            # httpx.HTTPError covers every transport/status failure; ValueError catches a malformed
            # JSON body. Best-effort (§9): degrade to SKIPPED, never abort the detection tick.
            _log.warning(
                "triggered_market_fetch_failed", external_market=external_market, error=str(exc)
            )
            return None
        if raw is None or bool(raw.get("closed", False)):
            return None
        try:
            market, snapshot = normalize_market(raw, self._clock())
        except (KeyError, ValueError, TypeError) as exc:
            _log.warning(
                "triggered_market_malformed", external_market=external_market, error=str(exc)
            )
            return None
        self._markets.upsert(market)
        self._snapshots.append(snapshot)
        _log.info("triggered_market_ingested", external_market=external_market, market_id=market.id)
        return market.id

    def run_market(self, market_id: str, asof: datetime, trader_ids: list[str]) -> str | None:
        # Evaluate-now (§5.5, R-12): score the market's CURRENT snapshot, never the historical
        # trigger price. ``asof`` is the trigger time, recorded on the candidate / logged for audit
        # only — a historical-asof replay would instead source latest_for_market_asof(asof).
        snapshot = self._snapshots.latest_for_market(market_id)
        if snapshot is None:
            _log.info("candidate_skipped_no_snapshot", market_id=market_id)
            return None
        if self._estimates.exists_for_snapshot(snapshot.id):
            # Already estimated at this snapshot — by the pipeline tick or a prior candidate. The
            # estimate id is deterministic on the snapshot (PYT-73), so re-running would collide on
            # estimates_pkey and crash the detection tick. Return the pre-existing estimate id so
            # the caller can link the FK (PYT-188, first-producer-wins on signal_source).
            existing = self._estimates.latest_for_market(market_id)
            # Return the id if found; a non-None snapshot but no estimate is a data inconsistency —
            # fall through to the pipeline run to produce one.
            if existing is not None:
                return existing.id
        market = self._markets.get(market_id)
        if market is None:
            return None
        _log.info(
            "candidate_evaluated",
            market_id=market_id,
            asof=asof.isoformat(),
            trader_count=len(trader_ids),
        )
        result = self._pipeline.run(market, snapshot)  # verdict persisted (§5.2)
        # On the veto-before-estimation path result.estimate is None — the pipeline still ran and
        # the candidate is EVALUATED. Return a sentinel that is truthy so ``if run_market(...)``
        # callers stay correct. The FK is set to the run_id in that edge case via a special marker
        # below; callers that unpack the estimate_id must tolerate None (FK stays NULL on veto).
        return result.estimate.id if result.estimate is not None else None

    def evaluate(
        self, external_market: str, asof: datetime, trader_ids: list[str]
    ) -> tuple[str, str | None] | None:
        market_id = self.resolve(external_market)
        if market_id is None:
            return None
        snapshot = self._snapshots.latest_for_market(market_id)
        if snapshot is None:
            return None
        estimate_id = self.run_market(market_id, asof, trader_ids)
        # run_market returns None only when there is no snapshot or no market row — both already
        # guarded above. After the guards, None means veto-before-estimation (no estimate produced).
        # The pipeline still ran, so the candidate is EVALUATED; estimate_id is None on that path.
        return (market_id, estimate_id)


def drain_queued(
    *, store: QueuedStore, evaluator: CandidateEvaluator, budget: int
) -> list[CandidateSignal]:
    """Re-evaluate over-cap QUEUED candidates oldest-first under the remaining daily budget (R-9).

    The daily cap is a hard ceiling, so this only runs when a tick leaves budget unspent; an
    untracked QUEUED candidate (no market_id) is left in place for a later, tracking-aware tick.
    """
    drained: list[CandidateSignal] = []
    for queued in store.list_by_status(CandidateSignalStatus.QUEUED):  # repo orders oldest-first
        if budget <= 0:
            break
        if queued.market_id is None:
            continue
        estimate_id = evaluator.run_market(queued.market_id, queued.asof, queued.trader_ids)
        if estimate_id is not None:
            drained.append(store.set_status(queued.id, CandidateSignalStatus.EVALUATED))
            store.set_estimate(queued.id, estimate_id)
            budget -= 1
    return drained


def detect_and_enqueue(
    traders: Sequence[TrackedTrader],
    *,
    falcon: FalconPort,
    since: datetime,
    entry_repo: TraderEntryRepo,
    store: CandidateRepo,
    evaluator: CandidateEvaluator,
    daily_cap: int,
    evaluated_today: int,
    now: datetime,
) -> list[CandidateSignal]:
    """Detect each active wallet's new entries, enqueue deduped candidates, then drain the backlog
    with any budget those new candidates left unspent (one tick of §5)."""
    triggers: list[TraderEntry] = []
    for trader in traders:
        triggers.extend(
            detect_new_entries(trader, falcon.get_trader_trades(trader.wallet, since), entry_repo)
        )
    new = enqueue_candidates(
        triggers,
        store=store,
        evaluator=evaluator,
        daily_cap=daily_cap,
        evaluated_today=evaluated_today,
        created_at=now,
    )
    used = sum(1 for signal in new if signal.status is CandidateSignalStatus.EVALUATED)
    drained = drain_queued(
        store=store, evaluator=evaluator, budget=max(0, daily_cap - evaluated_today - used)
    )
    return new + drained
