"""Shared venue-adapter plumbing (§4.10): transports, the §8.6 VPN gate, retry/backoff.

Mirrors the Polymarket crawler's pattern: a Protocol transport with a FixtureTransport (offline JSON
replay, the default) and live HTTP transports that classify a connection/geo error as
LiveVenueAccessError — surface it and ask the operator to enable the VPN, never retry blindly.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from pythia.common.logging import get_logger
from pythia.ingestion.market_crawler import (
    LiveVenueAccessError,
    TransientTransportError,
    require_live,
)

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VenueCandidate:
    external_ref: str
    question: str
    close_time: datetime | None
    yes_price: float | None


class VenueTransport(Protocol):
    def fetch(self) -> list[dict[str, Any]]: ...


class FixtureTransport:
    """Replays a recorded venue response from disk — the default offline transport."""

    def __init__(
        self, path: Path, extract: Callable[[Any], list[dict[str, Any]]] = lambda data: data
    ) -> None:
        self._path = path
        self._extract = extract

    def fetch(self) -> list[dict[str, Any]]:
        return list(self._extract(json.loads(self._path.read_text())))


def fetch_json(
    url: str,
    *,
    venue: str,
    use_live: bool,
    headers: dict[str, str] | None = None,
    params: dict[str, str | int] | None = None,
    timeout: float = 20.0,
) -> Any:
    require_live(use_live, venue)
    try:
        response = httpx.get(url, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.ConnectError as exc:  # a geo-block looks like a connection failure
        raise LiveVenueAccessError(
            f"cannot reach {venue} at {url}; enable the VPN before a live call (§8.6)"
        ) from exc
    except httpx.TimeoutException as exc:
        raise TransientTransportError(f"timeout calling {venue} at {url}") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            raise TransientTransportError(f"{exc.response.status_code} from {venue}") from exc
        raise
    return response.json()


class VenueClient:
    """Wraps a transport + a per-venue normalizer with backoff and the §8.6 VPN gate."""

    def __init__(
        self,
        transport: VenueTransport,
        to_candidate: Callable[[dict[str, Any]], VenueCandidate | None],
        *,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._transport = transport
        self._to_candidate = to_candidate
        self._sleep = sleep
        self._retries = retries
        self._backoff_base = backoff_base

    def _fetch(self) -> list[dict[str, Any]]:
        last: TransientTransportError | None = None
        for attempt in range(self._retries):
            try:
                return self._transport.fetch()
            except LiveVenueAccessError:
                raise
            except TransientTransportError as exc:
                last = exc
                _log.warning("venue_fetch_transient_failure", attempt=attempt + 1, error=str(exc))
                if attempt + 1 < self._retries:
                    self._sleep(self._backoff_base * (2**attempt))
        assert last is not None
        raise last

    def candidates(self) -> list[VenueCandidate]:
        out: list[VenueCandidate] = []
        for raw in self._fetch():
            try:
                candidate = self._to_candidate(raw)
            except (KeyError, ValueError, TypeError) as exc:
                _log.warning("malformed_venue_market_dropped", error=str(exc))
                continue
            if candidate is not None:
                out.append(candidate)
        return out
