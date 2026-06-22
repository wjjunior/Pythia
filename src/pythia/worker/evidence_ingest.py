"""Evidence-ingest tick: fetch articles for open markets and write to pgvector (§2.1, §4.5)."""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pythia.common import EmbeddingPort, Source
from pythia.common.logging import get_logger
from pythia.config import Settings
from pythia.db.repositories import MarketRepository, SourceRepository
from pythia.ingestion.evidence import EvidenceClient, EvidenceTransport
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

        return NewsApiTransport(settings.newsapi_key)
    _log.warning("unknown_evidence_source_skipped", source=name)
    return None


def ingest_tick(
    session: Session,
    embedder: EmbeddingPort,
    transports: list[EvidenceTransport],
    *,
    ts: datetime | None = None,
) -> int:
    """Fetch evidence articles for all open markets and persist via EvidenceStore.

    Returns the number of articles successfully persisted across all markets and sources.
    Idempotent: same ts + same content yields the same deterministic IDs — duplicate
    inserts are caught and skipped. A LiveVenueAccessError (VPN gate, §8.6) propagates.
    """
    if not transports:
        return 0
    tick_ts = ts or datetime.now(UTC)
    markets = MarketRepository(session).list(status="open")
    store = EvidenceStore(session, embedder)
    sources = SourceRepository(session)
    total = 0
    for transport in transports:
        client = EvidenceClient(transport)
        source = sources.upsert(
            Source(
                id=_source_id(transport.name),
                name=transport.name,
                kind="news",
                reliability=transport.reliability,
            )
        )
        for market in markets:
            try:
                articles = client.articles(market.question)
            except LiveVenueAccessError:
                raise
            except Exception as exc:
                _log.warning(
                    "evidence_fetch_failed",
                    source=transport.name,
                    market_id=market.id,
                    error=str(exc),
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
                    _log.debug(
                        "evidence_duplicate_skipped",
                        source=transport.name,
                        market_id=market.id,
                    )
    _log.info("evidence_ingest_tick", articles_written=total, sources=len(transports))
    return total


def _source_id(name: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"evidence-source:{name}"))
