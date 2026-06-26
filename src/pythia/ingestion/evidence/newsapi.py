"""NewsAPI adapter (§2.1): search-by-query news evidence, English language, ranked by relevancy."""

import contextlib
from datetime import datetime
from typing import Any

from pythia.ingestion.evidence.base import EvidenceArticle, fetch_json_evidence

SOURCE_NAME = "newsapi"
SOURCE_RELIABILITY = 0.6
_BASE_URL = "https://newsapi.org/v2/everything"
_PAGE_SIZE = 10


def to_article(raw: dict[str, Any]) -> EvidenceArticle | None:
    title = str(raw.get("title") or "")
    description = str(raw.get("description") or "")
    content = str(raw.get("content") or "")
    text = " ".join(part for part in (title, description, content) if part)
    if not text.strip():
        return None
    url: str | None = raw.get("url") or None
    published_raw = raw.get("publishedAt")
    published_at: datetime | None = None
    if published_raw:
        with contextlib.suppress(ValueError):
            published_at = datetime.fromisoformat(str(published_raw).replace("Z", "+00:00"))
    return EvidenceArticle(content=text, url=url, published_at=published_at)


class NewsApiTransport:
    """Live NewsAPI transport — not geo-restricted; requires NEWSAPI_KEY."""

    def __init__(
        self, api_key: str, *, use_live: bool = False, page_size: int = _PAGE_SIZE
    ) -> None:
        self._api_key = api_key
        self._use_live = use_live
        self._page_size = page_size
        self.name = SOURCE_NAME
        self.reliability = SOURCE_RELIABILITY

    def fetch(self, query: str) -> list[dict[str, Any]]:
        payload = fetch_json_evidence(
            _BASE_URL,
            source=SOURCE_NAME,
            use_live=self._use_live,
            params={
                "q": query,
                "language": "en",
                "sortBy": "relevancy",
                "pageSize": self._page_size,
                "apiKey": self._api_key,
            },
        )
        articles: list[dict[str, Any]] = payload.get("articles") or []
        return articles

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return to_article(raw)
