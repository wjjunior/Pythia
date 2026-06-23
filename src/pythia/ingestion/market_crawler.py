"""Polymarket market crawler: fetch Gamma markets and normalize to immutable snapshots.

The point-in-time backbone (§2.1, §3.4). Live access is VPN-gated (§8.6): a connection/geo
error raises LiveVenueAccessError (surface it and ask the operator to enable the VPN) rather than
retrying blindly; transient errors retry with backoff.
"""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from pythia.common import Market, MarketSnapshot
from pythia.common.logging import get_logger

_log = get_logger(__name__)


class LiveVenueAccessError(RuntimeError):
    """A live venue could not be reached — likely geo-blocked; enable the VPN (§8.6)."""


class TransientTransportError(RuntimeError):
    """A retryable provider error (timeout or 5xx)."""


class MarketNotFoundError(LookupError):
    """No market matched the requested external id."""


class MarketTransport(Protocol):
    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]: ...


class FixtureTransport:
    """Replays recorded Gamma responses from disk — the default offline transport."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = json.loads(self._path.read_text())
        return data


class HttpxTransport:
    """Live Gamma transport (VPN-gated). Classifies errors for the crawler's retry policy."""

    def __init__(
        self,
        base_url: str,
        *,
        limit: int = 100,
        timeout: float = 20.0,
        volume_min: float | None = None,
        max_markets: int = 500,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._limit = limit
        self._timeout = timeout
        self._volume_min = volume_min
        self._max_markets = max_markets

    def _request(self, params: dict[str, str | int | float]) -> list[dict[str, Any]]:
        url = f"{self._base_url}/markets"
        try:
            response = httpx.get(url, params=params, timeout=self._timeout)
            response.raise_for_status()
        except httpx.ConnectError as exc:  # includes ConnectTimeout — a geo-block looks like this
            raise LiveVenueAccessError(
                f"cannot reach {url}; enable the VPN before a live Polymarket call (§8.6)"
            ) from exc
        except httpx.TimeoutException as exc:
            raise TransientTransportError(f"timeout calling {url}") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise TransientTransportError(f"{exc.response.status_code} from {url}") from exc
            raise
        data: list[dict[str, Any]] = response.json()
        return data

    def fetch_markets(
        self,
        *,
        closed: bool = False,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
    ) -> list[dict[str, Any]]:
        base: dict[str, str | int | float] = {
            "limit": self._limit,
            "closed": "true" if closed else "false",
            "include_tag": "true",
        }
        if closed:
            return self._request(base)
        # Open crawl (§A.1): soonest-resolving, liquid markets first, paginated past one page so
        # short-term markets across every category surface — not just a default-ordered slice.
        base["order"] = "endDate"
        base["ascending"] = "true"
        if end_date_min is not None:
            base["end_date_min"] = end_date_min
        if end_date_max is not None:
            base["end_date_max"] = end_date_max
        if self._volume_min is not None:
            base["volume_num_min"] = self._volume_min
        markets: list[dict[str, Any]] = []
        offset = 0
        while len(markets) < self._max_markets:
            page = self._request({**base, "offset": offset})
            markets.extend(page)
            if len(page) < self._limit:
                break
            offset += self._limit
        return markets[: self._max_markets]


def _parse_json_list(value: str) -> list[Any]:
    parsed: list[Any] = json.loads(value)
    return parsed


def _yes_price(raw: dict[str, Any]) -> float:
    outcomes = _parse_json_list(raw["outcomes"])
    prices = _parse_json_list(raw["outcomePrices"])
    if len(outcomes) != len(prices):
        raise ValueError(f"outcomes/prices length mismatch: {len(outcomes)} vs {len(prices)}")
    for outcome, price in zip(outcomes, prices, strict=True):
        if str(outcome).lower() == "yes":
            return float(price)
    return float(raw["lastTradePrice"])


@dataclass(frozen=True, slots=True)
class ResolvedMarket:
    external_id: str
    outcome: int | None  # None = not cleanly resolved (ambiguous → excluded from Brier)
    resolved_at: datetime


def resolved_outcome(raw: dict[str, Any]) -> int | None:
    outcomes = _parse_json_list(raw["outcomes"])
    prices = _parse_json_list(raw["outcomePrices"])
    if len(outcomes) != len(prices):
        return None
    for outcome, price in zip(outcomes, prices, strict=True):
        if str(outcome).lower() == "yes":
            yes_price = float(price)
            if yes_price == 1.0:
                return 1
            if yes_price == 0.0:
                return 0
            return None
    return None


def _resolved_at(raw: dict[str, Any], fallback: datetime) -> datetime:
    end = raw.get("endDate")
    if end:
        try:
            return datetime.fromisoformat(end)
        except ValueError:
            pass
    return fallback


def _series_title(raw: dict[str, Any]) -> str | None:
    events = raw.get("events") or []
    series = events[0].get("series") or [] if events else []
    title = (series[0].get("title") or "").strip() if series else ""
    return title or None


def derive_category(raw: dict[str, Any]) -> str:
    explicit = (raw.get("category") or "").strip()
    if explicit:
        return explicit
    for tag in raw.get("tags") or []:
        label = (tag.get("label") or "").strip()
        if label:
            return label
    return _series_title(raw) or "uncategorized"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_datetime(value: str | None) -> datetime | None:
    # Gamma dates may arrive date-only (naive); normalize to UTC so day-deltas and SQL comparisons
    # against a UTC asof never mix naive and aware datetimes.
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _uma_resolves_at(raw: dict[str, Any]) -> datetime | None:
    for key in ("umaEndDate", "umaEndDateIso"):
        parsed = _parse_datetime(raw.get(key))
        if parsed is not None:
            return parsed
    return None


def _event_slug(raw: dict[str, Any]) -> str | None:
    # /event/<event-slug> is the page that resolves on Polymarket; the market slug is the fallback.
    events = raw.get("events") or []
    event = (events[0].get("slug") or "").strip() if events else ""
    return event or (raw.get("slug") or "").strip() or None


def to_market(raw: dict[str, Any], market_id: str) -> Market:
    return Market(
        id=market_id,
        external_id=raw["conditionId"],
        question=raw["question"],
        category=derive_category(raw),
        resolution_rule=raw.get("description", ""),
        resolution_source=raw.get("resolutionSource") or None,
        event_slug=_event_slug(raw),
        close_time=_parse_datetime(raw.get("endDate")),
        resolves_at=_uma_resolves_at(raw),
        resolved=bool(raw.get("closed", False)),
    )


def to_snapshot(
    raw: dict[str, Any], snapshot_id: str, market_id: str, ts: datetime
) -> MarketSnapshot:
    return MarketSnapshot(
        id=snapshot_id,
        market_id=market_id,
        ts=ts,
        yes_price=_yes_price(raw),
        spread=float(raw["spread"]),
        volume=float(raw["volumeNum"]),
        liquidity=float(raw["liquidityNum"]),
    )


def _market_id(external_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, external_id))


def _snapshot_id(external_id: str, ts: datetime) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{external_id}@{ts.isoformat()}"))


class MarketCrawler:
    """MarketDataPort adapter over a Gamma transport, with backoff and the §8.6 VPN gate."""

    def __init__(
        self,
        transport: MarketTransport,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
        backoff_base: float = 0.5,
        close_within_days: int | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._retries = retries
        self._backoff_base = backoff_base
        self._close_within_days = close_within_days

    def _end_date_max(self) -> str | None:
        if self._close_within_days is None or self._close_within_days <= 0:
            return None
        return (self._clock() + timedelta(days=self._close_within_days)).isoformat()

    def _fetch(self, *, closed: bool = False) -> list[dict[str, Any]]:
        end_date_max = None if closed else self._end_date_max()
        end_date_min = None if closed else self._clock().isoformat()
        last: TransientTransportError | None = None
        for attempt in range(self._retries):
            try:
                return self._transport.fetch_markets(
                    closed=closed, end_date_max=end_date_max, end_date_min=end_date_min
                )
            except LiveVenueAccessError:
                raise
            except TransientTransportError as exc:
                last = exc
                _log.warning("market_crawl_transient_failure", attempt=attempt + 1, error=str(exc))
                if attempt + 1 < self._retries:
                    self._sleep(self._backoff_base * (2**attempt))
        assert last is not None
        raise last

    def list_markets(self, **filters: object) -> list[Market]:
        markets: list[Market] = []
        for raw in self._fetch():
            try:
                markets.append(to_market(raw, _market_id(raw["conditionId"])))
            except (KeyError, ValueError, TypeError) as exc:
                _log.warning("malformed_market_dropped", error=str(exc))
        return markets

    def snapshot(self, external_id: str) -> MarketSnapshot:
        raw = next((m for m in self._fetch() if m.get("conditionId") == external_id), None)
        if raw is None:
            raise MarketNotFoundError(external_id)
        ts = self._clock()
        return to_snapshot(raw, _snapshot_id(external_id, ts), _market_id(external_id), ts)

    def poll(self) -> list[tuple[Market, MarketSnapshot]]:
        ts = self._clock()
        pairs: list[tuple[Market, MarketSnapshot]] = []
        for raw in self._fetch():
            try:
                external_id = raw["conditionId"]
                market = to_market(raw, _market_id(external_id))
                snapshot = to_snapshot(raw, _snapshot_id(external_id, ts), market.id, ts)
            except (KeyError, ValueError, TypeError) as exc:
                _log.warning("malformed_market_dropped", error=str(exc))
                continue
            pairs.append((market, snapshot))
        return pairs

    def resolutions(self) -> list[ResolvedMarket]:
        now = self._clock()
        resolved: list[ResolvedMarket] = []
        for raw in self._fetch(closed=True):
            try:
                resolved.append(
                    ResolvedMarket(
                        external_id=raw["conditionId"],
                        outcome=resolved_outcome(raw),
                        resolved_at=_resolved_at(raw, now),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                _log.warning("malformed_resolution_dropped", error=str(exc))
        return resolved
