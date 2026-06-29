"""Composition root: run the estimation pipeline on open markets (§2.3, AC-6/AC-8).

The only place outside tests that builds a `Pipeline`. It arms the cost guards from settings —
a per-market `BudgetGuard` (per-run cap + daily kill switch, tagging cost events with `market_id`)
and the `(market, snapshot, evidence_hash)` re-bill cache wrapping `make_llm_port` — and runs the
pipeline over every open market's latest snapshot. A score nudges the radar via `publish_radar`;
a breached budget halts the batch.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from pythia.agents import (
    BudgetExceeded,
    BudgetGuard,
    CachingLLMPort,
    make_llm_port,
)
from pythia.common import (
    Estimate,
    EventGroup,
    EvidencePort,
    LLMPort,
    Market,
    MarketStatus,
    PipelineFunnel,
    TraderEntry,
)
from pythia.common.ids import deterministic_id
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import (
    ArbRepository,
    CostRepository,
    EstimateRepository,
    MarketRepository,
    PipelineRunFunnelRepository,
    ShortlistCursorRepository,
    SnapshotRepository,
)
from pythia.db.smartmoney import CandidateSignalRepo, TraderEntryRepo
from pythia.orchestration.pipeline import Pipeline
from pythia.rag import EvidenceStore, make_embedder
from pythia.scoring.events import couple_probabilities, detect_arbitrage
from pythia.scoring.funnel import (
    RadarGate,
    classify_radar_gate,
    funnel_from_gates,
)
from pythia.scoring.prioritization import price_band_priority
from pythia.services.events import EventService
from pythia.smartmoney.evidence import SmartMoneyEvidencePort

_log = get_logger(__name__)

# market_id -> the LLMPort used for that market's pipeline run.
LlmFactory = Callable[[str], LLMPort]


@dataclass(frozen=True, slots=True)
class RunSummary:
    markets: int
    scored: int
    halted: bool
    arbs: int = 0


def run_event_pass(session: Session, *, now: datetime) -> int:
    """Group open markets by event, detect structural arbitrage, and refresh the stored set."""
    open_markets = MarketRepository(session).list(status=MarketStatus.OPEN)
    by_event: dict[str, int] = {}
    for market in open_markets:
        if market.event_slug:
            by_event[market.event_slug] = by_event.get(market.event_slug, 0) + 1
    service = EventService(session)
    arbs_repo = ArbRepository(session)
    estimates = EstimateRepository(session)
    total = 0
    for event_slug, option_count in by_event.items():
        if option_count < 2:
            continue  # a single-option event is not a coupled set
        group = service.get_event_group(event_slug, now)
        if group is None:
            continue
        arbs = detect_arbitrage(group)
        arbs_repo.replace_for_event(event_slug, arbs, detected_at=now)
        total += len(arbs)
        _couple_event(estimates, group)
    return total


_COUPLED_MODEL = "event-coupled"


def _couple_event(estimates: EstimateRepository, group: EventGroup) -> None:
    """Project the options' committed forecasts onto the valid set; append the coupled estimates.

    Append-only: a new ``event-coupled`` estimate per changed option becomes the latest forecast the
    calibration verdict measures (PYT-144). Idempotent — skip if the latest is already coupled.
    """
    latest: dict[str, Estimate] = {}
    for option in group.options:
        estimate = estimates.latest_for_market(option.market_id)
        if estimate is None or estimate.model_name == _COUPLED_MODEL:
            return  # incomplete, or already coupled at the latest forecast
        latest[option.market_id] = estimate
    committed = {
        market_id: (e.p_model_calibrated if e.p_model_calibrated is not None else e.p_model)
        for market_id, e in latest.items()
    }
    coupled = couple_probabilities(group, committed)
    for market_id, estimate in latest.items():
        if coupled[market_id] == committed[market_id]:
            continue  # projection left this option unchanged (already valid)
        estimates.add(
            Estimate(
                id=deterministic_id(_COUPLED_MODEL, estimate.snapshot_id),
                market_id=estimate.market_id,
                snapshot_id=estimate.snapshot_id,
                p_model=estimate.p_model,
                confidence=estimate.confidence,
                lower=estimate.lower,
                upper=estimate.upper,
                rationale="event-coupled forecast",
                evidence_ids=estimate.evidence_ids,
                model_name=_COUPLED_MODEL,
                critic_notes=estimate.critic_notes,
                p_model_calibrated=coupled[market_id],
                p_model_calibrated_fit_asof=estimate.p_model_calibrated_fit_asof,
                members=[],
            )
        )


def _default_llm_factory(session: Session, settings: Settings) -> LlmFactory:
    def build(market_id: str) -> LLMPort:
        guard = BudgetGuard(
            CostRepository(session),
            run_cap=settings.cost_cap_per_run,
            daily_cap=settings.cost_budget_daily,
            market_id=market_id,
        )
        return CachingLLMPort(
            make_llm_port(settings, cost_sink=guard.record, budget_check=guard.check)
        )

    return build


def _routing(settings: Settings) -> dict[str, str]:
    selection = settings.model_routing_strategy if settings.llm_enabled else "stub"
    return {"estimation": selection, "critic": selection}


class _RepoRosterFlow:
    def __init__(self, session: Session) -> None:
        self._entries = TraderEntryRepo(session)

    def active_on_market(self, market_id: str, *, asof: datetime) -> list[TraderEntry]:
        return self._entries.list_active_on_market(market_id, asof=asof)


class _RepoTriggeredMarkets:
    def __init__(self, session: Session) -> None:
        self._signals = CandidateSignalRepo(session)

    def is_triggered(self, market_id: str) -> bool:
        return self._signals.has_market_trigger(market_id)


def _evidence_port(session: Session, settings: Settings, base: EvidencePort) -> EvidencePort:
    # §5.4 optional feature: expose active-roster flow as estimation evidence, off by default.
    if not settings.smart_money_evidence_enabled:
        return base
    return SmartMoneyEvidencePort(
        base,
        flow=_RepoRosterFlow(session),
        triggers=_RepoTriggeredMarkets(session),
        enabled=True,
    )


def rotate_shortlist(
    markets: Sequence[Market],
    *,
    after_id: str | None,
    size: int,
    # Sort key over markets; default is id order (PYT-165). PYT-167 passes a (band, id) key to put
    # midband markets first. Any is justified: the key value is heterogeneous (a str or a tuple).
    key: Callable[[Market], Any] | None = None,
) -> tuple[list[Market], str | None]:
    """The next ``size``-market slice in ``key`` order after ``after_id``, wrapping (PYT-165/167).

    Returns the slice and the new cursor (the slice's last id). Advancing it each tick walks
    estimation coverage through the whole open set instead of re-picking the same markets. Pure
    selection — never reads a score, threshold, or risk value; it chooses *which* markets the
    pipeline judges and in what order, never *how* they are judged.
    """
    if not markets:
        return [], after_id
    sort_key = key if key is not None else (lambda market: market.id)
    ordered = sorted(markets, key=sort_key)
    count = len(ordered)
    start = 0
    if after_id is not None:
        # Resume after the cursor's market (matched by id, so the key order may change); if it has
        # left the open set (resolved), restart from the front — every market still gets covered.
        idx = next((i for i, market in enumerate(ordered) if market.id == after_id), None)
        start = idx + 1 if idx is not None else 0
    chosen = [ordered[(start + offset) % count] for offset in range(min(size, count))]
    return chosen, chosen[-1].id


def _shortlist(session: Session, settings: Settings, markets: list[Market]) -> list[Market]:
    size = settings.discovery_shortlist_size
    if size is None or size <= 0 or len(markets) <= size:
        return markets  # no throttle, or the whole open set already fits in one tick
    # PYT-167: order midband (calibration-measurable) markets ahead of near-certain tails WITHIN the
    # rotation — priority, not exclusion. Bias is by price band only; category is never a sort key.
    prices = SnapshotRepository(session).latest_yes_prices([market.id for market in markets])
    lo, hi = settings.eval_cohort_price_min, settings.eval_cohort_price_max

    def _band_then_id(market: Market) -> tuple[int, str]:
        price = prices.get(market.id, hi + 1.0)  # no snapshot → out of band → estimated later
        return (price_band_priority(price, lo=lo, hi=hi), market.id)

    cursor = ShortlistCursorRepository(session)
    chosen, new_after = rotate_shortlist(
        markets, after_id=cursor.get(), size=size, key=_band_then_id
    )
    cursor.set(new_after)
    _log.info("discovery_shortlist_rotated", open_markets=len(markets), shortlisted=len(chosen))
    return chosen


def run_open_markets(
    session: Session,
    settings: Settings,
    *,
    publish_radar: Callable[[str], None] | None = None,
    llm_factory: LlmFactory | None = None,
    funnel_sink: Callable[[PipelineFunnel], None] | None = None,
) -> RunSummary:
    asof = datetime.now(UTC)
    open_markets = MarketRepository(session).list(status=MarketStatus.OPEN)
    markets = _shortlist(session, settings, open_markets)
    snapshots = SnapshotRepository(session)
    estimates = EstimateRepository(session)
    evidence = _evidence_port(session, settings, EvidenceStore(session, make_embedder(settings)))
    build_llm = llm_factory if llm_factory is not None else _default_llm_factory(session, settings)
    routing = _routing(settings)
    # PYT-164: pure per-tick counting alongside the existing flow — no stage logic changes. crawled
    # and tracked_open coincide at the pipeline tick (crawl filters ran in the separate crawl tick).
    categories = Counter(market.category for market in open_markets)
    gates: Counter[RadarGate] = Counter()
    estimated = 0
    scored = 0

    def _emit_funnel() -> None:
        if funnel_sink is None:
            return
        funnel_sink(
            funnel_from_gates(
                crawled=len(open_markets),
                tracked_open=len(open_markets),
                shortlisted=len(markets),
                estimated=estimated,
                scored=scored,
                gates=gates,
                categories=categories,
            )
        )

    for market in markets:
        snapshot = snapshots.latest_for_market(market.id)
        if snapshot is None or estimates.exists_for_snapshot(snapshot.id):
            continue
        pipeline = Pipeline(
            session=session,
            llm=build_llm(market.id),
            evidence=evidence,
            settings=settings,
            routing=routing,
        )
        try:
            result = pipeline.run(market, snapshot)
        except BudgetExceeded:
            _log.warning("pipeline_budget_halt", market_id=market.id)
            _emit_funnel()
            return RunSummary(markets=len(markets), scored=scored, halted=True)
        except Exception:
            _log.exception("pipeline_market_failed", market_id=market.id)
            continue
        if result.estimate is None:
            continue  # vetoed before estimation (PipelineResult.estimate is None) — not estimated
        estimated += 1
        if result.score is not None:
            scored += 1
            gates[
                classify_radar_gate(
                    p_model=result.estimate.p_model,
                    q=snapshot.yes_price,
                    close_time=market.close_time,
                    resolves_at=market.resolves_at,
                    resolved=market.resolved,
                    asof=asof,
                )
            ] += 1
            if publish_radar is not None:
                publish_radar(market.id)
    _emit_funnel()
    arbs = run_event_pass(session, now=datetime.now(UTC))
    return RunSummary(markets=len(markets), scored=scored, halted=False, arbs=arbs)


def make_funnel_sink(session: Session) -> Callable[[PipelineFunnel], None]:
    """Persist the per-tick funnel and emit it as one structured log line (PYT-164)."""
    repo = PipelineRunFunnelRepository(session)

    def sink(funnel: PipelineFunnel) -> None:
        repo.add(funnel)
        _log.info("pipeline_funnel", **funnel.model_dump())

    return sink
