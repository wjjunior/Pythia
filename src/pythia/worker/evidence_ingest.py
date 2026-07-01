"""Evidence-ingest tick: fetch articles for open markets and write to pgvector (§2.1, §4.5)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from pythia.common import EmbeddingPort, MarketStatus, Source
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import MarketRepository, SourceRepository
from pythia.ingestion.evidence import (
    EvidenceClient,
    EvidenceRateLimitedError,
    EvidenceTransport,
    redact_credentials,
)
from pythia.ingestion.market_crawler import LiveVenueAccessError
from pythia.rag import EvidenceStore

_log = get_logger(__name__)


def build_evidence_transports(settings: Settings) -> list[EvidenceTransport]:
    """Build live evidence transports from settings — only sources in evidence_source_list."""
    transports: list[EvidenceTransport] = []
    for name in settings.evidence_source_list:
        transport = _make_transport(name, settings)
        if transport is not None:
            transports.append(transport)
    return transports


def _make_transport(name: str, settings: Settings) -> EvidenceTransport | None:
    if name == "newsapi":
        if not settings.newsapi_key:
            _log.warning("evidence_source_skipped", source=name, reason="no NEWSAPI_KEY configured")
            return None
        from pythia.ingestion.evidence.newsapi import NewsApiTransport

        return NewsApiTransport(
            settings.newsapi_key.get_secret_value(), use_live=settings.use_live_venues
        )
    if name == "newsdata":
        if not settings.newsdata_key:
            _log.warning(
                "evidence_source_skipped", source=name, reason="no NEWSDATA_KEY configured"
            )
            return None
        from pythia.ingestion.evidence.newsdata import NewsDataTransport

        return NewsDataTransport(
            settings.newsdata_key.get_secret_value(), use_live=settings.use_live_venues
        )
    if name == "mediastack":
        if not settings.mediastack_key:
            _log.warning(
                "evidence_source_skipped", source=name, reason="no MEDIASTACK_KEY configured"
            )
            return None
        from pythia.ingestion.evidence.mediastack import MediastackTransport

        return MediastackTransport(
            settings.mediastack_key.get_secret_value(), use_live=settings.use_live_venues
        )
    _log.warning("unknown_evidence_source_skipped", source=name)
    return None


def _markets_with_recent_evidence(session: Session, cutoff: datetime) -> set[str]:
    rows = session.exec(
        select(models.Evidence.market_id)
        .where(col(models.Evidence.observed_at) >= cutoff)
        .distinct()
    ).all()
    return {str(market_id) for market_id in rows}


def ingest_tick(
    session: Session,
    embedder: EmbeddingPort,
    transports: list[EvidenceTransport],
    *,
    ts: datetime | None = None,
    max_markets: int | None = None,
    refresh_hours: int = 24,
) -> int:
    """Fetch evidence for open markets that lack fresh evidence and persist via EvidenceStore.

    Free-tier aware (PYT-173): skips markets that already have evidence within ``refresh_hours``,
    bounds the burst to ``max_markets`` per tick, and assigns each market ONE source round-robin
    (not every source times every market). A 429 stops that source for the tick. Returns
    the count persisted. Idempotent per (market, source, ts). A LiveVenueAccessError propagates.
    """
    if not transports:
        return 0
    tick_ts = ts or datetime.now(UTC)
    recent = _markets_with_recent_evidence(session, tick_ts - timedelta(hours=refresh_hours))
    targets = [
        m for m in MarketRepository(session).list(status=MarketStatus.OPEN) if m.id not in recent
    ]
    if max_markets is not None:
        targets = targets[:max_markets]

    store = EvidenceStore(session, embedder)
    sources = SourceRepository(session)
    clients = [
        (
            transport.name,
            EvidenceClient(transport),
            sources.upsert(
                Source(
                    id=_source_id(transport.name),
                    name=transport.name,
                    kind="news",
                    reliability=transport.reliability,
                )
            ),
        )
        for transport in transports
    ]

    rate_limited: set[str] = set()
    total = 0
    rotation = 0
    for market in targets:
        chosen = _next_source(clients, rate_limited, rotation)
        if chosen is None:
            break  # every source is rate-limited this tick — stop rather than keep failing
        rotation, name, client, source = chosen
        try:
            articles = client.articles(market.question)
        except LiveVenueAccessError:
            raise
        except EvidenceRateLimitedError:
            rate_limited.add(name)
            _log.warning("evidence_rate_limited", source=name)
            continue  # this market retries next tick — it still has no fresh evidence
        except Exception as exc:
            _log.warning(
                "evidence_fetch_failed",
                source=name,
                market_id=market.id,
                error=redact_credentials(str(exc)),
            )
            continue
        for article in articles:
            try:
                store.add(
                    market_id=market.id,
                    source_id=source.id,
                    observed_at=tick_ts,
                    content=article.content,
                    url=article.url,
                )
                total += 1
            except IntegrityError:
                session.rollback()
                _log.debug("evidence_duplicate_skipped", source=name, market_id=market.id)
    _log.info(
        "evidence_ingest_tick", articles_written=total, targeted=len(targets), sources=len(clients)
    )
    return total


def _next_source(
    clients: list[tuple[str, EvidenceClient, Source]], rate_limited: set[str], rotation: int
) -> tuple[int, str, EvidenceClient, Source] | None:
    """Round-robin pick of the next non-rate-limited source; None if all are rate-limited."""
    for _ in range(len(clients)):
        name, client, source = clients[rotation % len(clients)]
        rotation += 1
        if name not in rate_limited:
            return rotation, name, client, source
    return None


def _source_id(name: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"evidence-source:{name}"))
