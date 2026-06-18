"""Worker entrypoint: schedule the market crawl on CRAWL_INTERVAL_SECONDS (§8.6 VPN-gated)."""

import logging

from pythia.common import Market, MarketSnapshot
from pythia.config import get_settings
from pythia.ingestion import HttpxTransport, MarketCrawler
from pythia.worker.scheduler import build_scheduler

_logger = logging.getLogger(__name__)


def _log_sink(pairs: list[tuple[Market, MarketSnapshot]]) -> None:
    _logger.info("market crawl tick: %d snapshots", len(pairs))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if not settings.use_live_venues:
        _logger.info(
            "USE_LIVE_VENUES is false — market crawl disabled; enable it and the VPN (§8.6)"
        )
        return
    crawler = MarketCrawler(HttpxTransport(settings.polymarket_api_base))
    scheduler = build_scheduler(crawler, _log_sink, settings.crawl_interval_seconds)
    _logger.info("starting market crawl every %ds", settings.crawl_interval_seconds)
    scheduler.start()


if __name__ == "__main__":
    main()
