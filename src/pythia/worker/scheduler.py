"""APScheduler wiring for the market-crawl heartbeat (§CRAWL_INTERVAL_SECONDS)."""

from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler

from pythia.common import Market, MarketSnapshot
from pythia.ingestion import MarketCrawler

Sink = Callable[[list[tuple[Market, MarketSnapshot]]], None]


def tick(crawler: MarketCrawler, sink: Sink) -> None:
    sink(crawler.poll())


def build_scheduler(crawler: MarketCrawler, sink: Sink, interval_seconds: int) -> BlockingScheduler:
    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: tick(crawler, sink),
        "interval",
        seconds=interval_seconds,
        id="market-crawl",
    )
    return scheduler
