"""Resolution-ingest tick: record objective Polymarket resolutions for tracked markets (§5, PYT-69).

Only outcomes the venue has officially resolved are recorded as calibration ground truth — never
LLM-guessed ones, which would corrupt Brier(system) vs reality. A market that is closed but not
cleanly resolved is recorded ambiguous (excluded from Brier). Runs in the worker, so no SSE.
"""

from sqlmodel import Session

from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import MarketRepository, ResolutionRepository
from pythia.ingestion.market_crawler import MarketCrawler
from pythia.orchestration import ResolutionService
from pythia.paper import PaperLedger

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
