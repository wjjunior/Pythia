"""Mediastack adapter (§2.1): live news; key is query-param only, so logs must redact (PYT-173)."""

from typing import Any

from pythia.ingestion.evidence.base import (
    EvidenceArticle,
    fetch_json_evidence,
    parse_iso_datetime,
)

SOURCE_NAME = "mediastack"
SOURCE_RELIABILITY = 0.5  # description-only (no full body) — the weakest of the three sources
_BASE_URL = "https://api.mediastack.com/v1/news"
_LIMIT = 10


def to_article(raw: dict[str, Any]) -> EvidenceArticle | None:
    title = str(raw.get("title") or "")
    description = str(raw.get("description") or "")
    text = " ".join(part for part in (title, description) if part)
    if not text.strip():
        return None
    url: str | None = raw.get("url") or None
    published_at = parse_iso_datetime(raw.get("published_at"))
    return EvidenceArticle(content=text, url=url, published_at=published_at)


class MediastackTransport:
    """Live Mediastack transport — not geo-restricted; MEDIASTACK_KEY via access_key query."""

    def __init__(self, api_key: str, *, use_live: bool = False, limit: int = _LIMIT) -> None:
        self._api_key = api_key
        self._use_live = use_live
        self._limit = limit
        self.name = SOURCE_NAME
        self.reliability = SOURCE_RELIABILITY
        self._fetch_json = fetch_json_evidence

    def fetch(self, query: str) -> list[dict[str, Any]]:
        payload = self._fetch_json(
            _BASE_URL,
            source=SOURCE_NAME,
            use_live=self._use_live,
            params={
                "access_key": self._api_key,
                "keywords": query,
                "languages": "en",
                "limit": self._limit,
            },
        )
        data: list[dict[str, Any]] = payload.get("data") or []
        return data

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return to_article(raw)
