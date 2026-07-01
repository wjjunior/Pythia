"""NewsData.io adapter (§2.1): latest-news search; header auth keeps the key out of the URL."""

from typing import Any

from pythia.ingestion.evidence.base import (
    EvidenceArticle,
    fetch_json_evidence,
    parse_iso_datetime,
)

SOURCE_NAME = "newsdata"
SOURCE_RELIABILITY = 0.6
_BASE_URL = "https://newsdata.io/api/1/latest"


def to_article(raw: dict[str, Any]) -> EvidenceArticle | None:
    title = str(raw.get("title") or "")
    description = str(raw.get("description") or "")
    content = str(raw.get("content") or "")
    text = " ".join(part for part in (title, description, content) if part)
    if not text.strip():
        return None
    url: str | None = raw.get("link") or None
    return EvidenceArticle(
        content=text, url=url, published_at=parse_iso_datetime(raw.get("pubDate"))
    )


class NewsDataTransport:
    """Live NewsData.io transport — not geo-restricted; NEWSDATA_KEY via X-ACCESS-KEY header."""

    def __init__(self, api_key: str, *, use_live: bool = False) -> None:
        self._api_key = api_key
        self._use_live = use_live
        self.name = SOURCE_NAME
        self.reliability = SOURCE_RELIABILITY
        self._fetch_json = fetch_json_evidence

    def fetch(self, query: str) -> list[dict[str, Any]]:
        payload = self._fetch_json(
            _BASE_URL,
            source=SOURCE_NAME,
            use_live=self._use_live,
            params={"q": query, "language": "en"},
            headers={"X-ACCESS-KEY": self._api_key},
        )
        results: list[dict[str, Any]] = payload.get("results") or []
        return results

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return to_article(raw)
