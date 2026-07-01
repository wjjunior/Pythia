"""Unit tests (PYT-173): NewsData.io + Mediastack article parsing and header/query auth."""

from datetime import UTC, datetime
from typing import Any

from pythia.ingestion.evidence import mediastack, newsdata


def test_newsdata_to_article_joins_title_description_content() -> None:
    article = newsdata.to_article(
        {
            "title": "Governor race tightens",
            "description": "Polls narrow",
            "content": "Full body of the article.",
            "link": "https://ex.com/a",
            "pubDate": "2026-05-04 06:08:09",
        }
    )
    assert article is not None
    assert article.content == "Governor race tightens Polls narrow Full body of the article."
    assert article.url == "https://ex.com/a"
    assert article.published_at == datetime(2026, 5, 4, 6, 8, 9, tzinfo=UTC)


def test_newsdata_to_article_is_none_when_empty() -> None:
    assert newsdata.to_article({"title": "", "description": "", "content": ""}) is None


def test_newsdata_transport_uses_header_auth_not_query() -> None:
    transport = newsdata.NewsDataTransport("SECRET123")
    captured: dict[str, Any] = {}

    def _fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"results": []}

    transport._fetch_json = _fake_fetch
    transport.fetch("weather")

    assert captured["headers"] == {"X-ACCESS-KEY": "SECRET123"}
    assert "apikey" not in captured.get("params", {})  # key never in the query string


def test_mediastack_to_article_joins_title_description() -> None:
    article = mediastack.to_article(
        {
            "title": "Nadal out",
            "description": "Withdraws over COVID concerns",
            "url": "https://ex.com/b",
            "published_at": "2020-08-05T05:47:24+00:00",
        }
    )
    assert article is not None
    assert article.content == "Nadal out Withdraws over COVID concerns"
    assert article.url == "https://ex.com/b"
    assert article.published_at == datetime(2020, 8, 5, 5, 47, 24, tzinfo=UTC)


def test_mediastack_transport_passes_access_key_as_query() -> None:
    transport = mediastack.MediastackTransport("SECRET123")
    captured: dict[str, Any] = {}

    def _fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"data": []}

    transport._fetch_json = _fake_fetch
    transport.fetch("tennis")

    params = captured["params"]
    assert params["access_key"] == "SECRET123"  # mediastack only supports query-param auth
    assert params["keywords"] == "tennis"
