"""Evidence source adapters (§2.1, §4.5): dumb pipes that fetch and normalize articles.

Mirrors the venue-adapter pattern: a Protocol transport, an offline FixtureTransport (default),
and live HTTP transports that raise LiveVenueAccessError on geo-block — surface it and ask the
operator to enable the VPN (§8.6) rather than retrying blindly.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pythia.common.logging import get_logger
from pythia.ingestion.market_crawler import LiveVenueAccessError, TransientTransportError

_log = get_logger(__name__)


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
    params: dict[str, str | int] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    import httpx

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
    except httpx.ConnectError as exc:
        raise LiveVenueAccessError(
            f"cannot reach {source} at {url}; enable the VPN before a live call (§8.6)"
        ) from exc
    except httpx.TimeoutException as exc:
        raise TransientTransportError(f"timeout calling {source} at {url}") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            raise TransientTransportError(f"{exc.response.status_code} from {source}") from exc
        raise
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
