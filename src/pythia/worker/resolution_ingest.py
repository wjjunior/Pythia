"""Resolution-ingest tick: record objective Polymarket resolutions for tracked markets (§5, PYT-69).

Only outcomes the venue has officially resolved are recorded as calibration ground truth — never
LLM-guessed ones, which would corrupt Brier(system) vs reality. A market that is closed but not
cleanly resolved is recorded ambiguous (excluded from Brier). Runs in the worker, so no SSE.
"""

from datetime import datetime

from sqlmodel import Session

from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import MarketRepository, ResolutionRepository
from pythia.ingestion.market_crawler import MarketCrawler, resolved_outcome
from pythia.orchestration import ResolutionService
from pythia.paper import PaperLedger
from pythia.smartmoney.candidate import MarketFetcher

_log = get_logger(__name__)

_RESOLUTION_SOURCE = "polymarket"


def ingest_tick(session: Session, settings: Settings, crawler: MarketCrawler) -> int:
    markets = MarketRepository(session)
    resolutions = ResolutionRepository(session)
    service = ResolutionService(session, settings, PaperLedger(session))
    recorded = 0
    for resolved in crawler.resolutions():
        market = markets.get_by_external_id(resolved.external_id)
        if market is None or resolutions.exists(market.id):
            continue
        service.record(
            market.id,
            resolved.outcome if resolved.outcome is not None else 0,
            at=resolved.resolved_at,
            ambiguous=resolved.outcome is None,
            source=_RESOLUTION_SOURCE,
        )
        recorded += 1
    _log.info("resolution_ingest_tick", recorded=recorded)
    return recorded


def backfill_resolutions(
    session: Session,
    settings: Settings,
    fetcher: MarketFetcher,
    *,
    now: datetime,
    limit: int | None = None,
) -> int:
    """Record objective Gamma resolutions for estimated markets that ended but carry no resolution
    row — the calibration ground truth the window-bounded crawl misses (PYT-187). Each is fetched
    by condition_id with closed=true (PYT-182); a closed-but-unresolved market is left for a later
    tick, never invented. `limit` bounds a periodic tick; None drains the backlog (the one-off)."""
    markets = MarketRepository(session).estimated_ended_unresolved(now=now, limit=limit)
    resolutions = ResolutionRepository(session)
    service = ResolutionService(session, settings, PaperLedger(session))
    recorded = 0
    for market in markets:
        if resolutions.exists(market.id):
            continue
        raw = fetcher.fetch_market_by_condition(market.external_id, include_closed=True)
        if raw is None or not bool(raw.get("closed", False)):
            continue
        outcome = resolved_outcome(raw)
        if outcome is None:
            continue  # closed but not cleanly resolved — leave for a later tick, never invent
        service.record(market.id, outcome, at=now, ambiguous=False, source=_RESOLUTION_SOURCE)
        recorded += 1
    _log.info("resolution_backfill", recorded=recorded, candidates=len(markets))
    return recorded
