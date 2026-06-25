"""Open-position backfill on tracking start (v2 §5.5, AC-S8).

When a wallet enters monitoring we don't only wait for its next move — we sweep the positions it
**already holds** and ask whether *entering now* is +EV. Each open position is evaluated at
``asof = now`` against the current market price, **never** the trader's historical entry time or
price; most come back not viable because the price already converged, which is the expected,
correct result. The sweep honours the market-class allow-list (§3.4), ``BACKFILL_MAX_POSITIONS``
per wallet and the shared ``CANDIDATE_DAILY_CAP``, and dedupes against new-entry candidates within
the current bucket (``new_entry`` wins, §5.5).
"""

from collections.abc import Collection, Sequence
from datetime import UTC, datetime, time
from typing import Protocol

from sqlmodel import Session

from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    Market,
    TraderState,
)
from pythia.common.ids import deterministic_id
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import MarketRepository
from pythia.db.smartmoney import CandidateSignalRepo
from pythia.ingestion.falcon import FalconPort, OpenPosition
from pythia.orchestration import Pipeline
from pythia.smartmoney.candidate import (
    CandidateEvaluator,
    CandidateStore,
    PipelineCandidateEvaluator,
)

_log = get_logger(__name__)


class MarketResolver(Protocol):
    def get_by_external_id(self, external_id: str) -> Market | None: ...


class TriggeredMarkets(Protocol):
    def has_market_trigger(self, market_id: str, *, since: datetime | None = None) -> bool: ...


def should_backfill(to_state: TraderState, *, on_track: bool, on_promote: bool) -> bool:
    # A wallet entering trial (on_track) or promoted to active (on_promote) gets a one-time sweep.
    if to_state is TraderState.TRIAL:
        return on_track
    if to_state is TraderState.ACTIVE:
        return on_promote
    return False


def _bucket_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


def run_backfill_sweep(
    trader_id: str,
    positions: Sequence[OpenPosition],
    *,
    markets: MarketResolver,
    signals: TriggeredMarkets,
    store: CandidateStore,
    evaluator: CandidateEvaluator,
    allow: Collection[str],
    max_positions: int,
    daily_cap: int,
    evaluated_today: int,
    now: datetime,
) -> list[CandidateSignal]:
    """Evaluate a wallet's open book at ``asof = now`` (current price), one candidate per market."""
    bucket = _bucket_start(now)
    budget = max(0, daily_cap - evaluated_today)
    seen: set[str] = set()
    out: list[CandidateSignal] = []
    for position in positions:
        if len(out) >= max_positions:
            break  # BACKFILL_MAX_POSITIONS per wallet
        market = markets.get_by_external_id(position.condition_id)
        if market is None or market.category not in allow:
            continue  # untracked, or off the research-amenable allow-list (§3.4)
        if market.id in seen:
            continue  # one candidate per market within the sweep
        seen.add(market.id)
        if signals.has_market_trigger(market.id, since=bucket):
            continue  # dedupe (§5.5): a new_entry or prior sweep already owns this market-bucket
        signal_id = deterministic_id("open_backfill", market.id, bucket.isoformat())
        if store.get(signal_id) is not None:
            continue

        evaluated_market: str | None = None
        status = CandidateSignalStatus.QUEUED  # over the daily cap → deferred
        if budget > 0:
            # asof = now: the evaluator runs the pipeline at the market's CURRENT snapshot, never
            # the trader's historical entry price/time (§5.5, AC-S8).
            evaluated_market = evaluator.evaluate(position.condition_id, now, [trader_id])
            status = (
                CandidateSignalStatus.EVALUATED
                if evaluated_market
                else CandidateSignalStatus.SKIPPED
            )
            if evaluated_market is not None:
                budget -= 1
        out.append(
            store.enqueue(
                CandidateSignal(
                    id=signal_id,
                    market_id=market.id,
                    asof=now,
                    trader_ids=[trader_id],
                    source=CandidateSource.OPEN_BACKFILL,
                    status=status,
                    created_at=now,
                )
            )
        )
    return out


class BackfillSweeper:
    """Wires Falcon + repos + the v1 pipeline to sweep a wallet's open book on tracking start."""

    def __init__(
        self, session: Session, falcon: FalconPort, pipeline: Pipeline, settings: Settings
    ) -> None:
        self._falcon = falcon
        self._markets = MarketRepository(session)
        self._signals = CandidateSignalRepo(session)
        self._evaluator = PipelineCandidateEvaluator(session, pipeline)
        self._allow = frozenset(
            c.strip() for c in settings.market_class_allow.split(",") if c.strip()
        )
        self._max_positions = settings.backfill_max_positions
        self._daily_cap = settings.candidate_daily_cap

    def sweep(
        self, trader_id: str, wallet: str, *, now: datetime, evaluated_today: int
    ) -> list[CandidateSignal]:
        positions = self._falcon.get_open_positions(wallet)
        _log.info("backfill_sweep_start", trader_id=trader_id, positions=len(positions))
        return run_backfill_sweep(
            trader_id,
            positions,
            markets=self._markets,
            signals=self._signals,
            store=self._signals,
            evaluator=self._evaluator,
            allow=self._allow,
            max_positions=self._max_positions,
            daily_cap=self._daily_cap,
            evaluated_today=evaluated_today,
            now=now,
        )
