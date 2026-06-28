"""Polymarket data-api positions adapter (§4.5, §8.6, PYT-157).

The Falcon analytics gateway has no open-positions endpoint, so a tracked wallet's CURRENT holdings
come from Polymarket's data-api: ``GET {base}/positions?user={wallet}`` (no auth). Mirrors the venue
pattern — a Transport Protocol with a FixturePositionsTransport (offline cassette replay, the
default) and a live httpx transport that classifies a geo/connection error as
``LiveVenueAccessError`` (§8.6). ``PolymarketPositionsClient`` maps wire rows to neutral
``HeldPosition`` DTOs at the boundary and is fail-soft: data-api down yields no positions, never a
crash; a geo block re-raises so the operator is prompted (§8.6).
"""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from pythia.common import HeldPosition
from pythia.common.logging import get_logger
from pythia.ingestion.market_crawler import (
    LiveVenueAccessError,
    TransientTransportError,
    require_live,
)

_log = get_logger(__name__)

_DEFAULT_TIMEOUT = 20.0
_DEFAULT_RETRIES = 3
_DEFAULT_BACKOFF = 0.5


class PositionsTransport(Protocol):
    def fetch_positions(self, wallet: str) -> list[dict[str, Any]]: ...


class FixturePositionsTransport:
    """Replays a recorded data-api positions response from disk — the default offline transport."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch_positions(self, wallet: str) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = json.loads(self._path.read_text())
        return data


class HttpxPositionsTransport:
    """Live data-api transport (§8.6-gated). Classifies errors for the client's fail-soft policy."""

    def __init__(
        self,
        base_url: str,
        *,
        use_live: bool = False,
        timeout: float = _DEFAULT_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = _DEFAULT_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._use_live = use_live
        self._timeout = timeout
        self._sleep = sleep
        self._retries = retries
        self._backoff_base = backoff_base

    def fetch_positions(self, wallet: str) -> list[dict[str, Any]]:
        require_live(self._use_live, "polymarket")  # §8.6 defense-in-depth: refuse before any GET
        url = f"{self._base_url}/positions"
        last: TransientTransportError | None = None
        for attempt in range(self._retries):
            try:
                response = httpx.get(url, params={"user": wallet}, timeout=self._timeout)
                response.raise_for_status()
                data: list[dict[str, Any]] = response.json()
                return data
            except httpx.ConnectError as exc:  # a geo block looks like a connection failure
                raise LiveVenueAccessError(
                    f"cannot reach {url}; enable the VPN before a live Polymarket call (§8.6)"
                ) from exc
            except httpx.TimeoutException as exc:
                last = TransientTransportError(f"timeout calling {url}")
                _log.warning("positions_transient_failure", attempt=attempt + 1, error=str(exc))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last = TransientTransportError(f"{exc.response.status_code} from {url}")
                _log.warning("positions_transient_failure", attempt=attempt + 1, error=str(exc))
            if attempt + 1 < self._retries:
                self._sleep(self._backoff_base * (2**attempt))
        assert last is not None
        raise last


# A resolved market settles its price to exactly 0.0 (lost) or 1.0 (won); an open near-certain
# market sits at 0.99x, never exactly 1.0. `redeemable` flags a settled, claimable win. data-api
# /positions returns resolved-but-unredeemed holdings, so these are filtered to keep only the
# wallet's CURRENT open exposure (PYT-157).
_RESOLVED_PRICES = (0.0, 1.0)


def _is_open(row: dict[str, Any]) -> bool:
    if bool(row.get("redeemable", False)):
        return False
    return float(row.get("curPrice", 0.0)) not in _RESOLVED_PRICES


def _to_held(row: dict[str, Any]) -> HeldPosition:
    # Map the data-api wire shape to the neutral DTO at the boundary (§5). Numeric fields arrive as
    # JSON numbers; end_date is ISO8601 (or absent) — Pydantic coerces / validates.
    return HeldPosition(
        condition_id=row["conditionId"],
        outcome=row["outcome"],
        size=row["size"],
        avg_price=row["avgPrice"],
        cur_price=row["curPrice"],
        current_value=row["currentValue"],
        cash_pnl=row["cashPnl"],
        title=row["title"],
        end_date=row.get("endDate"),
    )


class PolymarketPositionsClient:
    """Implements PositionsPort over a transport, parsing DTOs at the boundary. Fail-soft (§9)."""

    def __init__(self, transport: PositionsTransport) -> None:
        self._t = transport

    def current_positions(self, wallet: str) -> list[HeldPosition]:
        try:
            rows = self._t.fetch_positions(wallet)
        except TransientTransportError as exc:  # data-api down → no positions, never a crash (§9)
            _log.warning("positions_unavailable", wallet=wallet, error=str(exc))
            return []
        # LiveVenueAccessError propagates: a geo block must surface to the operator (§8.6).
        held: list[HeldPosition] = []
        for row in rows:
            if not _is_open(row):
                continue  # resolved holding (settled or redeemable) — not current open exposure
            try:
                held.append(_to_held(row))
            except (KeyError, ValueError, TypeError) as exc:
                _log.warning("malformed_position_dropped", wallet=wallet, error=str(exc))
        return held


def make_positions_client(
    *, base_url: str, use_live: bool, fixtures_path: Path | None = None
) -> PolymarketPositionsClient:
    """Live transport when enabled; otherwise replay a fixture cassette offline."""
    if use_live:
        return PolymarketPositionsClient(HttpxPositionsTransport(base_url, use_live=use_live))
    if fixtures_path is None:
        raise ValueError("fixtures_path is required for the offline positions transport")
    return PolymarketPositionsClient(FixturePositionsTransport(fixtures_path))
