"""Cross-venue quote ingest tick: match open markets to venue candidates, persist quotes."""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pythia.common import MarketStatus, Venue
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import MarketRepository, VenueQuoteRepository
from pythia.ingestion.venues import VenueClient, quote_for_market

_log = get_logger(__name__)


def build_venue_clients(settings: Settings) -> list[tuple[Venue, VenueClient]]:
    """Build live venue clients from settings — only venues in cross_venue_source_list."""
    clients: list[tuple[Venue, VenueClient]] = []
    for name in settings.cross_venue_source_list:
        client = _make_client(name, settings)
        if client is not None:
            clients.append(client)
    return clients


def _make_client(name: str, settings: Settings) -> tuple[Venue, VenueClient] | None:
    if name == Venue.KALSHI:
        from pythia.ingestion.venues.kalshi import KalshiHttpTransport, to_candidate

        return Venue.KALSHI, VenueClient(KalshiHttpTransport(), to_candidate)
    if name == Venue.MANIFOLD:
        from pythia.ingestion.venues.manifold import ManifoldHttpTransport, to_candidate

        return Venue.MANIFOLD, VenueClient(ManifoldHttpTransport(), to_candidate)
    if name == Venue.METACULUS:
        if not settings.metaculus_api_key:
            _log.warning("venue_skipped", venue=name, reason="no METACULUS_API_KEY configured")
            return None
        from pythia.ingestion.venues.metaculus import MetaculusHttpTransport, to_candidate

        return (
            Venue.METACULUS,
            VenueClient(
                MetaculusHttpTransport(settings.metaculus_api_key.get_secret_value()), to_candidate
            ),
        )
    _log.warning("unknown_venue_skipped", venue=name)
    return None


def ingest_tick(
    session: Session,
    clients: list[tuple[Venue, VenueClient]],
    *,
    ts: datetime | None = None,
) -> int:
    """Fetch cross-venue quotes for all open markets and persist matches.

    Returns the number of new quotes persisted. Duplicate quotes (same venue + ref + ts) are
    skipped silently — this is idempotent by design.
    """
    if not clients:
        return 0
    tick_ts = ts or datetime.now(UTC)
    markets = MarketRepository(session).list(status=MarketStatus.OPEN)
    repo = VenueQuoteRepository(session)
    total = 0
    for venue, client in clients:
        try:
            candidates = client.candidates()
        except Exception as exc:
            _log.warning("venue_candidates_failed", venue=venue, error=str(exc))
            continue
        for market in markets:
            quote = quote_for_market(market, venue, candidates, tick_ts)
            if quote is None:
                continue
            try:
                repo.add(quote)
                total += 1
            except IntegrityError:
                session.rollback()
                _log.debug("venue_quote_duplicate_skipped", venue=venue, market_id=market.id)
    _log.info("venue_ingest_tick", quotes_persisted=total, venues=len(clients))
    return total
