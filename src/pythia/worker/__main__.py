"""Worker entrypoint: schedule the market crawl on CRAWL_INTERVAL_SECONDS (§8.6 VPN-gated)."""

from pythia.common import Market, MarketSnapshot
from pythia.common.logging import configure_logging, get_logger
from pythia.config import Settings, get_settings
from pythia.ingestion import HttpxTransport, MarketCrawler
from pythia.worker.scheduler import build_scheduler

_log = get_logger(__name__)


def _log_sink(pairs: list[tuple[Market, MarketSnapshot]]) -> None:
    _log.info("market_crawl_tick", snapshots=len(pairs))


def _configure(settings: Settings) -> None:
    configure_logging(
        service="worker",
        level=settings.log_level,
        json_logs=settings.log_json,
        logfire_token=settings.logfire_token,
        environment=settings.environment,
    )


def main() -> None:
    settings = get_settings()
    _configure(settings)
    if not settings.use_live_venues:
        _log.info("market_crawl_disabled", reason="USE_LIVE_VENUES is false; enable VPN (§8.6)")
        return
    crawler = MarketCrawler(HttpxTransport(settings.polymarket_api_base))
    scheduler = build_scheduler(crawler, _log_sink, settings.crawl_interval_seconds)
    _log.info("market_crawl_start", interval_seconds=settings.crawl_interval_seconds)
    scheduler.start()


if __name__ == "__main__":
    main()
