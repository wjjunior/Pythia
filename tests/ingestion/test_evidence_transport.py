"""Unit tests (PYT-173): credential redaction + 429 circuit-break in the evidence HTTP layer."""

from typing import Any

import httpx
import pytest

from pythia.ingestion.evidence.base import (
    EvidenceFetchError,
    EvidenceRateLimitedError,
    fetch_json_evidence,
    redact_credentials,
)


def test_redact_masks_api_keys_and_access_keys_in_query_strings() -> None:
    url = "https://newsapi.org/v2/everything?q=weather&apiKey=SECRET123&language=en"
    redacted = redact_credentials(url)
    assert "SECRET123" not in redacted
    assert "apiKey=***" in redacted
    assert "q=weather" in redacted  # non-credential params survive


def test_redact_covers_the_common_credential_param_names() -> None:
    for token in ("apiKey", "apikey", "api_key", "access_key", "X-ACCESS-KEY", "token"):
        assert "SECRET" not in redact_credentials(f"https://x?{token}=SECRET&a=1")


def _mock_httpx(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    def _get(url: str, **_: Any) -> httpx.Response:
        return httpx.Response(status_code=status, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _get)


def test_429_raises_rate_limited_without_leaking_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_httpx(monkeypatch, 429)
    with pytest.raises(EvidenceRateLimitedError) as excinfo:
        fetch_json_evidence(
            "https://api.mediastack.com/v1/news",
            source="mediastack",
            use_live=True,
            params={"access_key": "SECRET123", "keywords": "x"},
        )
    assert "SECRET123" not in str(excinfo.value)


def test_other_4xx_raises_fetch_error_without_leaking_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_httpx(monkeypatch, 401)
    with pytest.raises(EvidenceFetchError) as excinfo:
        fetch_json_evidence(
            "https://newsdata.io/api/1/latest",
            source="newsdata",
            use_live=True,
            params={"apikey": "SECRET123", "q": "x"},
        )
    assert "SECRET123" not in str(excinfo.value)
