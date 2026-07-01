"""Evidence source adapters (§2.1, §4.5): dumb pipes that fetch and normalize articles.

Mirrors the venue-adapter pattern: a Protocol transport, an offline FixtureTransport (default),
and live HTTP transports that raise LiveVenueAccessError on geo-block — surface it and ask the
operator to enable the VPN (§8.6) rather than retrying blindly.
"""

import contextlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pythia.common.logging import get_logger
from pythia.ingestion.market_crawler import (
    LiveVenueAccessError,
    TransientTransportError,
    require_live,
)

_log = get_logger(__name__)


class EvidenceRateLimitedError(Exception):
    """A source returned 429 — stop calling it for this tick (circuit-break) rather than hammer."""


class EvidenceFetchError(Exception):
    """A non-retryable evidence fetch failure (e.g. 401/403); the message never carries a key."""


_CREDENTIAL_QUERY_RE = re.compile(r"(?i)(api_?key|access_key|x-access-key|token)=[^&\s\"']+")


def redact_credentials(text: str) -> str:
    """Mask API keys/tokens in query strings before logging — some providers only take the key as a
    query param, so the URL (and any error string built from it) must be sanitized (rule 12)."""
    return _CREDENTIAL_QUERY_RE.sub(lambda match: f"{match.group(1)}=***", text)


def parse_iso_datetime(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse; naive means UTC. None on absence or parse failure."""
    if not value:
        return None
    with contextlib.suppress(ValueError):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


@dataclass(frozen=True, slots=True)
class EvidenceArticle:
    content: str
    url: str | None
    published_at: datetime | None


class EvidenceTransport(Protocol):
    name: str
    reliability: float

    def fetch(self, query: str) -> list[dict[str, Any]]: ...
    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None: ...


class FixtureEvidenceTransport:
    """Replays a recorded response from disk — the default offline transport for CI."""

    def __init__(
        self,
        path: Path,
        *,
        to_article: Callable[[dict[str, Any]], EvidenceArticle | None],
        extract: Callable[[Any], list[dict[str, Any]]] = lambda data: data,
        source_name: str,
        source_reliability: float,
    ) -> None:
        self._path = path
        self._to_article = to_article
        self._extract = extract
        self.name = source_name
        self.reliability = source_reliability

    def fetch(self, query: str) -> list[dict[str, Any]]:
        return list(self._extract(json.loads(self._path.read_text())))

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return self._to_article(raw)


def fetch_json_evidence(
    url: str,
    *,
    source: str,
    use_live: bool,
    params: dict[str, str | int] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    require_live(use_live, source)  # §8.6 defense-in-depth: no live egress unless the flag is on
    import httpx

    safe_url = redact_credentials(url)  # base url carries no key today, but redact defensively
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
    except httpx.ConnectError as exc:
        raise LiveVenueAccessError(
            f"cannot reach {source} at {safe_url}; enable the VPN before a live call (§8.6)"
        ) from exc
    except httpx.TimeoutException as exc:
        raise TransientTransportError(f"timeout calling {source} at {safe_url}") from exc
    except httpx.HTTPStatusError as exc:
        # Raise clean, credential-free messages — never re-raise the httpx error, whose str()
        # carries the full URL with query-param keys (the PYT-173 leak). Drop the cause too.
        status = exc.response.status_code
        if status == 429:
            raise EvidenceRateLimitedError(f"{source} rate-limited (429)") from None
        if status >= 500:
            raise TransientTransportError(f"{status} from {source}") from None
        raise EvidenceFetchError(f"{status} from {source}") from None
    return response.json()


class EvidenceClient:
    """Wraps a transport with retry/backoff and the §8.6 VPN gate."""

    def __init__(
        self,
        transport: EvidenceTransport,
        *,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._retries = retries
        self._backoff_base = backoff_base

    @property
    def name(self) -> str:
        return self._transport.name

    @property
    def reliability(self) -> float:
        return self._transport.reliability

    def fetch(self, query: str) -> list[dict[str, Any]]:
        return self._transport.fetch(query)

    def to_article(self, raw: dict[str, Any]) -> EvidenceArticle | None:
        return self._transport.to_article(raw)

    def articles(self, query: str) -> list[EvidenceArticle]:
        last: TransientTransportError | None = None
        for attempt in range(self._retries):
            try:
                raws = self._transport.fetch(query)
                break
            except LiveVenueAccessError:
                raise
            except TransientTransportError as exc:
                last = exc
                _log.warning(
                    "evidence_fetch_transient_failure",
                    source=self.name,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt + 1 < self._retries:
                    self._sleep(self._backoff_base * (2**attempt))
        else:
            assert last is not None
            raise last
        out: list[EvidenceArticle] = []
        for raw in raws:
            try:
                article = self._transport.to_article(raw)
            except (KeyError, ValueError, TypeError) as exc:
                _log.warning("malformed_evidence_article_dropped", source=self.name, error=str(exc))
                continue
            if article is not None:
                out.append(article)
        return out
