"""Worker entrypoint: market crawl + venue + evidence-ingest on CRAWL_INTERVAL_SECONDS (§8.6)."""

from pythia.common.logging import configure_logging, get_logger
from pythia.config import Settings, get_settings
from pythia.db.session import session_scope
from pythia.ingestion import HttpxTransport, MarketCrawler
from pythia.ingestion.falcon import FalconClient, make_falcon_client
from pythia.ingestion.polymarket_positions import (
    PolymarketPositionsClient,
    make_positions_client,
)
from pythia.rag.embeddings import make_embedder
from pythia.worker.evidence_ingest import build_evidence_transports
from pythia.worker.reconcile import reconcile_on_startup
from pythia.worker.scheduler import build_scheduler, make_persist_sink
from pythia.worker.venue_ingest import build_venue_clients

_log = get_logger(__name__)


def _falcon(settings: Settings) -> FalconClient | None:
    # Smart-money runs only when Falcon is credentialed (the worker is USE_LIVE_VENUES-gated).
    if not (settings.falcon_api_base and settings.falcon_api_token):
        _log.info("smartmoney_disabled", reason="FALCON_API_BASE/TOKEN unset")
        return None
    return make_falcon_client(
        base_url=settings.falcon_api_base,
        token=settings.falcon_api_token.get_secret_value(),
        use_live=settings.use_live_venues,
        agent_ids=settings.falcon_agent_id_overrides,
    )


def _positions(settings: Settings) -> PolymarketPositionsClient:
    # Current-holdings sweep over the §8.6-gated data-api; the worker only runs when live (§8.6).
    return make_positions_client(
        base_url=settings.polymarket_data_api_base, use_live=settings.use_live_venues
    )


def _configure(settings: Settings) -> None:
    configure_logging(
        service="worker",
        level=settings.log_level,
        json_logs=settings.log_json,
        logfire_token=(
            settings.logfire_token.get_secret_value() if settings.logfire_token else None
        ),
        environment=settings.environment,
    )


def main() -> None:
    settings = get_settings()
    _configure(settings)
    if not settings.use_live_venues:
        _log.info("worker_disabled", reason="USE_LIVE_VENUES is false; enable VPN (§8.6)")
        return
    crawler = MarketCrawler(
        HttpxTransport(
            settings.polymarket_api_base,
            use_live=settings.use_live_venues,
            volume_min=settings.crawl_volume_min,
            max_markets=settings.crawl_max_markets,
        ),
        close_within_days=settings.crawl_close_within_days,
    )
    venue_clients = build_venue_clients(settings)
    evidence_transports = build_evidence_transports(settings)
    embedder = make_embedder(settings)
    falcon = _falcon(settings)
    scheduler = build_scheduler(
        crawler,
        make_persist_sink(session_scope),
        settings.crawl_interval_seconds,
        session_factory=session_scope,
        venue_clients=venue_clients,
        embedder=embedder,
        evidence_transports=evidence_transports,
        settings=settings,
        falcon=falcon,
        positions=_positions(settings),
    )
    _log.info(
        "worker_start",
        interval_seconds=settings.crawl_interval_seconds,
        venue_count=len(venue_clients),
        evidence_source_count=len(evidence_transports),
    )
    reconcile_on_startup(session_scope, settings, crawler)  # settle current positions first (§5)
    scheduler.start()


if __name__ == "__main__":
    main()
