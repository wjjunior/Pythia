"""Startup reconciliation (PYT-175): the worker is not 24/7, so on (re)start it settles open paper
positions against resolutions that landed while it was offline — before the scheduler starts
discovering new opportunities. Best-effort: a venue error (VPN off, §8.6) is logged, not fatal."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime

from sqlmodel import Session

from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import PaperTradeRepository, ResolutionRepository
from pythia.ingestion.market_crawler import (
    LiveVenueAccessError,
    MarketCrawler,
    TransientTransportError,
    resolved_outcome,
)
from pythia.orchestration import ResolutionService
from pythia.paper import PaperLedger
from pythia.smartmoney.candidate import MarketFetcher
from pythia.worker.resolution_ingest import ingest_tick as resolution_ingest_tick

_log = get_logger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]
_RESOLUTION_SOURCE = "polymarket"


def settle_stale_positions(
    session: Session, settings: Settings, fetcher: MarketFetcher, *, now: datetime
) -> int:
    """Settle OPEN positions whose market has closed but resolution-ingest never caught — its
    discovery is bounded by the crawl's closed-market window, so an older closed market's position
    stays open forever (PYT-181). Re-check each such market on Gamma directly and settle it if it
    has a clean outcome; a closed-but-unresolved (ambiguous) market is left open, not invented."""
    markets = PaperTradeRepository(session).open_on_closed_markets(now=now)
    resolutions = ResolutionRepository(session)
    service = ResolutionService(session, settings, PaperLedger(session))
    settled = 0
    for market in markets:
        if resolutions.exists(market.id):
            continue
        raw = fetcher.fetch_market_by_condition(market.external_id)
        if raw is None or not bool(raw.get("closed", False)):
            continue
        outcome = resolved_outcome(raw)
        if outcome is None:
            continue  # closed but not cleanly resolved yet — leave open, don't invent a result
        service.record(market.id, outcome, at=now, ambiguous=False, source=_RESOLUTION_SOURCE)
        settled += 1
    return settled


def reconcile_on_startup(
    session_factory: SessionFactory,
    settings: Settings,
    crawler: MarketCrawler,
    *,
    fetcher: MarketFetcher | None = None,
) -> None:
    """Settle open paper positions on resolutions that happened while the worker was offline, before
    any new-opportunity discovery. A venue error is logged and the worker still starts — the
    resolution-ingest interval job retries; never blind-retry a geo-block here (§8.6)."""
    try:
        with session_factory() as session:
            settled = resolution_ingest_tick(session, settings, crawler)
            stale = (
                settle_stale_positions(session, settings, fetcher, now=datetime.now(UTC))
                if fetcher is not None
                else 0
            )
    except (LiveVenueAccessError, TransientTransportError) as exc:
        _log.warning("startup_reconcile_skipped", reason="venue unreachable", error=str(exc))
        return
    _log.info("startup_reconciled", settled=settled, stale_settled=stale)
