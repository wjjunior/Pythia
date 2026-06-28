"""Smart-money worker loop (v2 §5.1 to §5.3, §5.5): make the Falcon subsystem actually run.

Two ticks, composed here against the v1 cost-guarded pipeline:

- ``run_detection`` — monitored wallets' fresh entries (Falcon trades) → candidates → pipeline,
  bounded by ``CANDIDATE_DAILY_CAP``.
- ``run_roster_review`` — discover trial wallets, score them (the PYT-96 harness adapters), apply
  bounded promote/demote/evict transitions, and sweep open positions on tracking-start (§5.5).

Fail-soft (§9): Falcon unavailable yields no candidates and never crashes the worker; a geo block
(§8.6) is logged, not retried. Every pipeline run shares the per-run cap + daily kill switch.
"""

from datetime import UTC, datetime, time, timedelta

from sqlmodel import Session

from pythia.agents import (
    BudgetExceeded,
    BudgetGuard,
    CachingLLMPort,
    make_llm_port,
)
from pythia.common import LLMPort, PositionsPort, TrackedTrader, TraderState
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import CostRepository
from pythia.db.smartmoney import (
    CandidateSignalRepo,
    RosterEventRepo,
    TrackedTraderRepo,
    TraderEntryRepo,
    TraderPositionRepo,
)
from pythia.ingestion.falcon import FalconPort
from pythia.ingestion.market_crawler import HttpxTransport, LiveVenueAccessError
from pythia.orchestration import Pipeline
from pythia.rag import EvidenceStore, make_embedder
from pythia.smartmoney.backfill import BackfillSweeper, should_backfill
from pythia.smartmoney.candidate import PipelineCandidateEvaluator, detect_and_enqueue
from pythia.smartmoney.roster import RosterManager, RosterTransition, plan_transitions
from pythia.smartmoney.scoring import build_selection_harness
from pythia.smartmoney.selection import roster_policy

_log = get_logger(__name__)

# Falcon-trade lookback per detection tick; the detector is idempotent on (trader, market, ts), so a
# generous window only re-fetches, never duplicates.
DETECTION_LOOKBACK = timedelta(days=1)
_MONITORED = (TraderState.ACTIVE, TraderState.TRIAL)


def _start_of_day(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)


def _routing(settings: Settings) -> dict[str, str]:
    selection = settings.model_routing_strategy if settings.llm_enabled else "stub"
    return {"estimation": selection, "critic": selection}


def _guarded_llm(session: Session, settings: Settings) -> LLMPort:
    guard = BudgetGuard(
        CostRepository(session),
        run_cap=settings.cost_cap_per_run,
        daily_cap=settings.cost_budget_daily,
    )
    return CachingLLMPort(make_llm_port(settings, cost_sink=guard.record, budget_check=guard.check))


def _build_pipeline(session: Session, settings: Settings, llm: LLMPort) -> Pipeline:
    return Pipeline(
        session=session,
        llm=llm,
        evidence=EvidenceStore(session, make_embedder(settings)),
        settings=settings,
        routing=_routing(settings),
    )


def run_detection(
    session: Session, settings: Settings, falcon: FalconPort, *, now: datetime
) -> None:
    """One detection tick: monitored wallets' new entries → candidates → cost-guarded pipeline."""
    traders = [t for t in TrackedTraderRepo(session).list() if t.state in _MONITORED]
    if not traders:
        return
    signals = CandidateSignalRepo(session)
    pipeline = _build_pipeline(session, settings, _guarded_llm(session, settings))
    # On-demand ingestion (PYT-156): a trigger on a market outside our crawl is fetched + evaluated.
    # Only when live venues are on — offline this stays a local-only resolve, no fetch attempts.
    fetcher = (
        HttpxTransport(settings.polymarket_api_base, use_live=True)
        if settings.use_live_venues
        else None
    )
    evaluator = PipelineCandidateEvaluator(
        session, pipeline, market_fetcher=fetcher, clock=lambda: now
    )
    try:
        enqueued = detect_and_enqueue(
            traders,
            falcon=falcon,
            since=now - DETECTION_LOOKBACK,
            entry_repo=TraderEntryRepo(session),
            store=signals,
            evaluator=evaluator,
            daily_cap=settings.candidate_daily_cap,
            evaluated_today=signals.count_evaluated_since(_start_of_day(now)),
            now=now,
        )
    except LiveVenueAccessError:
        _log.warning("smartmoney_geo_block", tick="detection")  # §8.6: surface, never retry blindly
        return
    except BudgetExceeded:
        _log.warning("smartmoney_detection_budget_halt")
        return
    _log.info("smartmoney_detection_tick", traders=len(traders), candidates=len(enqueued))


def run_positions_sweep(session: Session, positions: PositionsPort, *, now: datetime) -> None:
    """One positions tick (§5, PYT-157): refresh each tracked wallet's current data-api holding.

    Pure visibility — positions feed no scoring/evidence path in v1. A retired wallet is cleared
    (no fetch) so retirement reliably empties its current-positions set, never leaving stale rows.
    Fail-soft (§9): a geo block is surfaced and the sweep stops (§8.6); the client already swallows
    a transient data-api outage.
    """
    traders = TrackedTraderRepo(session).list()
    if not traders:
        return
    repo = TraderPositionRepo(session)
    swept = 0
    for trader in traders:
        if trader.state is TraderState.RETIRED:
            repo.replace_for_trader(trader.id, [], asof=now)  # clear stale rows, no network call
            continue
        try:
            held = positions.current_positions(trader.wallet)
        except LiveVenueAccessError:
            _log.warning("smartmoney_geo_block", tick="positions")  # §8.6: surface, never retry
            return
        swept += len(repo.replace_for_trader(trader.id, held, asof=now))
    _log.info("smartmoney_positions_sweep", traders=len(traders), positions=swept)


def run_roster_review(
    session: Session, settings: Settings, falcon: FalconPort, *, now: datetime
) -> None:
    """One roster-review tick: discover → score → transition → backfill on tracking start."""
    traders_repo = TrackedTraderRepo(session)
    manager = RosterManager(traders_repo, RosterEventRepo(session))
    policy = roster_policy(settings)
    llm = _guarded_llm(session, settings)
    try:
        non_retired = [t for t in traders_repo.list() if t.state is not TraderState.RETIRED]
        added = manager.discover(
            falcon,
            at=now,
            limit=policy.roster_max,
            capacity=max(0, policy.roster_max - len(non_retired)),
        )
        harness = build_selection_harness(
            session,
            falcon=falcon,
            llm=llm,
            evidence=EvidenceStore(session, make_embedder(settings)),
            settings=settings,
        )
        roster = non_retired + added  # discoveries enter as trial, i.e. non-retired
        cards = harness.review(roster, asof=now)
    except LiveVenueAccessError:
        _log.warning("smartmoney_geo_block", tick="roster_review")
        return
    except BudgetExceeded:
        _log.warning("smartmoney_review_budget_halt")
        return
    scorecards = {card.trader_id: card for card in cards}
    transitions = plan_transitions(roster, scorecards, policy, asof=now)
    manager.apply(transitions, at=now)
    _log.info(
        "smartmoney_roster_review_tick",
        discovered=len(added),
        scored=len(cards),
        transitions=len(transitions),
    )
    _run_backfills(session, settings, falcon, llm, added, transitions, now=now)


def _run_backfills(
    session: Session,
    settings: Settings,
    falcon: FalconPort,
    llm: LLMPort,
    added: list[TrackedTrader],
    transitions: list[RosterTransition],
    *,
    now: datetime,
) -> None:
    if not (settings.backfill_on_track or settings.backfill_on_promote):
        return
    traders_repo = TrackedTraderRepo(session)
    targets: list[tuple[str, str]] = []
    for trader in added:  # freshly discovered wallets enter as trial
        if should_backfill(
            TraderState.TRIAL,
            on_track=settings.backfill_on_track,
            on_promote=settings.backfill_on_promote,
        ):
            targets.append((trader.id, trader.wallet))
    for transition in transitions:
        if transition.to_state is TraderState.ACTIVE and should_backfill(
            TraderState.ACTIVE,
            on_track=settings.backfill_on_track,
            on_promote=settings.backfill_on_promote,
        ):
            promoted = traders_repo.get(transition.trader_id)
            if promoted is not None:
                targets.append((promoted.id, promoted.wallet))
    if not targets:
        return
    sweeper = BackfillSweeper(session, falcon, _build_pipeline(session, settings, llm), settings)
    signals = CandidateSignalRepo(session)
    for trader_id, wallet in targets:
        try:
            swept = sweeper.sweep(
                trader_id,
                wallet,
                now=now,
                evaluated_today=signals.count_evaluated_since(_start_of_day(now)),
            )
        except LiveVenueAccessError:
            _log.warning("smartmoney_geo_block", tick="backfill")
            return
        except BudgetExceeded:
            _log.warning("smartmoney_backfill_budget_halt")
            return
        _log.info("smartmoney_backfill_sweep", trader_id=trader_id, candidates=len(swept))
