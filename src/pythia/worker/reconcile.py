"""Startup reconciliation (PYT-175): the worker is not 24/7, so on (re)start it settles open paper
positions against resolutions that landed while it was offline — before the scheduler starts
discovering new opportunities. Best-effort: a venue error (VPN off, §8.6) is logged, not fatal."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlmodel import Session

from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.ingestion.market_crawler import (
    LiveVenueAccessError,
    MarketCrawler,
    TransientTransportError,
)
from pythia.worker.resolution_ingest import ingest_tick as resolution_ingest_tick

_log = get_logger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]


def reconcile_on_startup(
    session_factory: SessionFactory, settings: Settings, crawler: MarketCrawler
) -> None:
    """Settle open paper positions on resolutions that happened while the worker was offline, before
    any new-opportunity discovery. A venue error is logged and the worker still starts — the
    resolution-ingest interval job retries; never blind-retry a geo-block here (§8.6)."""
    try:
        with session_factory() as session:
            settled = resolution_ingest_tick(session, settings, crawler)
    except (LiveVenueAccessError, TransientTransportError) as exc:
        _log.warning("startup_reconcile_skipped", reason="venue unreachable", error=str(exc))
        return
    _log.info("startup_reconciled", settled=settled)
