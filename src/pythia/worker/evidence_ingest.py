"""Evidence-ingest tick: fetch articles for open markets and write to pgvector (§2.1, §4.5)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from pythia.common import EmbeddingPort, Market, MarketStatus, Source
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
from pythia.orchestration.runner import select_shortlist
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
    if name == "open-meteo":
        return _make_weather_transport(settings)  # no key; needs no config to run offline fixtures
    _log.warning("unknown_evidence_source_skipped", source=name)
    return None


def _make_weather_transport(settings: Settings) -> EvidenceTransport:
    from pythia.ingestion.evidence.weather import (
        CompositeExtractor,
        LLMWeatherExtractor,
        RegexWeatherExtractor,
        WeatherQueryExtractor,
        WeatherTransport,
    )

    extractors: list[WeatherQueryExtractor] = [RegexWeatherExtractor()]
    if settings.openrouter_api_key:  # cheap-LLM fallback only when a key is configured
        from pythia.agents.llm_port import HttpxChatTransport

        chat = HttpxChatTransport(
            settings.openrouter_api_key.get_secret_value(), settings.openrouter_base_url
        )
        extractors.append(LLMWeatherExtractor(chat, settings.weather_extract_model))
    return WeatherTransport(CompositeExtractor(extractors), use_live=settings.use_live_venues)


def _markets_with_recent_evidence(
    session: Session, market_ids: list[str], cutoff: datetime
) -> set[str]:
    # Scope by market_id so the composite (market_id, observed_at) index is used — an observed_at
    # filter alone can't use the leading column and would scan the growing evidence table.
    if not market_ids:
        return set()
    rows = session.exec(
        select(models.Evidence.market_id)
        .where(col(models.Evidence.market_id).in_([uuid.UUID(m) for m in market_ids]))
        .where(col(models.Evidence.observed_at) >= cutoff)
        .distinct()
    ).all()
    return {str(market_id) for market_id in rows}


def _select_targets(
    session: Session,
    *,
    now: datetime,
    cutoff: datetime,
    max_markets: int | None,
    settings: Settings | None,
) -> list[Market]:
    open_markets = MarketRepository(session).list(status=MarketStatus.OPEN)
    # Peek the SAME shortlist window the pipeline will estimate next (PYT-176) — read-only, so the
    # rotation cursor is untouched. Without settings, fall back to the whole open set.
    window = (
        select_shortlist(session, settings, open_markets, advance=False)
        if settings is not None
        else open_markets
    )
    # Drop closed legs (a closed market can't be entered → wasted budget, PYT-174) and, fetch-once,
    # those with evidence since cutoff.
    open_window = [m for m in window if m.close_time is None or m.close_time >= now]
    recent = _markets_with_recent_evidence(session, [m.id for m in open_window], cutoff)
    candidates = [market for market in open_window if market.id not in recent]
    return candidates[:max_markets] if max_markets is not None else candidates


def ingest_tick(
    session: Session,
    embedder: EmbeddingPort,
    transports: list[EvidenceTransport],
    *,
    ts: datetime | None = None,
    max_markets: int | None = None,
    refresh_hours: int = 24,
    settings: Settings | None = None,
) -> int:
    """Fetch evidence for the markets the pipeline will score and persist via EvidenceStore.

    Free-tier aware (PYT-173/174/176): targets the peeked discovery-shortlist window (the exact
    markets the pipeline estimates next), skips closed markets and those with evidence within
    ``refresh_hours``, bounds the burst to ``max_markets`` per tick, and assigns each market ONE
    source round-robin (not every source times every market). A 429 stops that source for the tick.
    Returns the count persisted; idempotent per (market, source, ts). Venue errors propagate.
    """
    if not transports:
        return 0
    tick_ts = ts or datetime.now(UTC)
    targets = _select_targets(
        session,
        now=tick_ts,
        cutoff=tick_ts - timedelta(hours=refresh_hours),
        max_markets=max_markets,
        settings=settings,
    )

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
            getattr(
                transport, "categories", None
            ),  # domain source ⇒ its categories; None ⇒ general
        )
        for transport in transports
    ]

    rate_limited: set[str] = set()
    total = 0
    rotation = 0
    for market in targets:
        chosen = _next_source(clients, rate_limited, rotation, market.category)
        if chosen is None:
            continue  # this market's eligible sources are all rate-limited — others may still run
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


_Client = tuple[str, EvidenceClient, Source, "frozenset[str] | None"]


def _next_source(
    clients: list[_Client], rate_limited: set[str], rotation: int, category: str
) -> tuple[int, str, EvidenceClient, Source] | None:
    """Route by category (PYT-178): a domain source serves only its categories, a general source
    (categories None) serves the rest. Round-robin the next non-rate-limited eligible source."""
    specific = [c for c in clients if c[3] is not None and category.lower() in c[3]]
    eligible = specific if specific else [c for c in clients if c[3] is None]
    if not eligible:
        return None
    for _ in range(len(eligible)):
        name, client, source, _categories = eligible[rotation % len(eligible)]
        rotation += 1
        if name not in rate_limited:
            return rotation, name, client, source
    return None


def _source_id(name: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"evidence-source:{name}"))
