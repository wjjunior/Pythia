"""Falcon adapter (v2 §3.1, §4.5): read-only smart-money analytics over a POST gateway.

Mirrors the v1 venue pattern — a Transport Protocol with a FixtureTransport (offline cassette
replay, the default) and a live httpx transport that classifies a geo/connection error as
``LiveVenueAccessError`` (§8.6). ``FalconClient`` maps the §4.5 port methods onto Falcon datasets,
parses DTOs at the boundary, and is **fail-soft**: Falcon down yields no data, never a crash — the
v1 path keeps running. A geo block is re-raised so the operator is prompted (§8.6).

The live API is a semantic-retrieve gateway: ``POST {FALCON_API_BASE}`` (the full endpoint URL,
e.g. ``.../api/v2/semantic/retrieve/parameterized``) with ``{agent_id, params, pagination,
formatter_config}`` and a Bearer token. ``agent_id`` selects the dataset (top-traders = 579).
"""

import json
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

import httpx

from pythia.common.logging import get_logger
from pythia.ingestion.falcon.models import (
    FalconMarket,
    FalconTrade,
    OpenPosition,
    Orderbook,
    TopTrader,
    TraderPnlPoint,
)
from pythia.ingestion.market_crawler import LiveVenueAccessError, TransientTransportError

_log = get_logger(__name__)

FalconDataset = Literal[
    "top_traders", "trader_pnl", "trader_trades", "market", "orderbook", "open_positions"
]

_PNL_LOOKBACK_DAYS = 30
_DEFAULT_PERIOD = "7d"
_DEFAULT_LIMIT = 50

T = TypeVar("T")


@runtime_checkable
class FalconPort(Protocol):
    def list_top_traders(
        self, *, period: str = _DEFAULT_PERIOD, limit: int = _DEFAULT_LIMIT
    ) -> list[TopTrader]: ...
    def get_trader_pnl(self, wallet: str, asof: datetime) -> list[TraderPnlPoint]: ...
    def get_trader_trades(self, wallet: str, since: datetime) -> list[FalconTrade]: ...
    def get_market(self, condition_id: str) -> FalconMarket | None: ...
    def get_orderbook(self, token_id: str) -> Orderbook | None: ...
    def get_open_positions(self, wallet: str) -> list[OpenPosition]: ...


class FalconTransport(Protocol):
    def retrieve(self, dataset: FalconDataset, params: dict[str, Any]) -> Any: ...


class FixtureFalconTransport:
    """Replays a recorded Falcon response per dataset from disk — the default offline transport."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    def retrieve(self, dataset: FalconDataset, params: dict[str, Any]) -> Any:
        return json.loads((self._dir / f"{dataset}.json").read_text())


class HttpxFalconTransport:
    """Live Falcon transport (USE_LIVE_VENUES + token gated). POSTs the semantic gateway."""

    def __init__(
        self,
        base_url: str,
        token: str,
        agent_ids: Mapping[FalconDataset, int],
        *,
        limit: int = 100,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._url = base_url.rstrip("/")  # FALCON_API_BASE is the full semantic-retrieve endpoint
        self._token = token
        self._agent_ids = agent_ids
        self._limit = limit
        self._timeout = timeout
        self._sleep = sleep
        self._retries = retries
        self._backoff_base = backoff_base

    def _post(self, body: dict[str, Any]) -> Any:
        last: TransientTransportError | None = None
        for attempt in range(self._retries):
            try:
                response = httpx.post(
                    self._url,
                    json=body,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=self._timeout,
                )
                response.raise_for_status()
                return response.json()
            except httpx.ConnectError as exc:  # a geo block looks like a connection failure
                raise LiveVenueAccessError(
                    f"cannot reach Falcon at {self._url}; verify reachability / VPN (§8.6)"
                ) from exc
            except httpx.TimeoutException as exc:
                last = TransientTransportError(f"timeout calling Falcon at {self._url}")
                _log.warning("falcon_transient_failure", attempt=attempt + 1, error=str(exc))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last = TransientTransportError(f"{exc.response.status_code} from Falcon")
                _log.warning("falcon_transient_failure", attempt=attempt + 1, error=str(exc))
            if attempt + 1 < self._retries:
                self._sleep(self._backoff_base * (2**attempt))
        assert last is not None
        raise last

    def retrieve(self, dataset: FalconDataset, params: dict[str, Any]) -> Any:
        agent_id = self._agent_ids.get(dataset)
        if agent_id is None:  # unknown agent_id → no data (fail-soft), not a KeyError crash
            raise TransientTransportError(f"no Falcon agent_id configured for '{dataset}'")
        if dataset == "orderbook":  # flat response, no envelope / pagination
            return self._post({"agent_id": agent_id, "params": params})
        results: list[dict[str, Any]] = []
        offset = 0
        while True:
            body = {
                "agent_id": agent_id,
                "params": params,
                "pagination": {"limit": self._limit, "offset": offset},
                "formatter_config": {"format_type": "raw"},
            }
            page = self._post(body)
            results.extend(page.get("data", {}).get("results", []))
            if not page.get("pagination", {}).get("has_more"):
                break
            offset += self._limit
        return {"data": {"results": results}}


def _results(raw: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = raw.get("data", {}).get("results", [])
    return results


class FalconClient:
    """Implements FalconPort over a FalconTransport, parsing DTOs at the boundary. Fail-soft."""

    def __init__(self, transport: FalconTransport) -> None:
        self._t = transport

    def _soft(self, dataset: FalconDataset, params: dict[str, Any], default: T) -> Any | T:
        try:
            return self._t.retrieve(dataset, params)
        except TransientTransportError as exc:  # Falcon down → no data, never a crash (§9)
            _log.warning("falcon_unavailable", dataset=dataset, error=str(exc))
            return default
        # LiveVenueAccessError propagates: a geo block must surface to the operator (§8.6).

    def list_top_traders(
        self, *, period: str = _DEFAULT_PERIOD, limit: int = _DEFAULT_LIMIT
    ) -> list[TopTrader]:
        raw = self._soft(
            "top_traders", {"wallet_address": "ALL", "leaderboard_period": period}, default=None
        )
        return [TopTrader.model_validate(row) for row in _results(raw)][:limit] if raw else []

    def get_trader_pnl(self, wallet: str, asof: datetime) -> list[TraderPnlPoint]:
        params = {
            "wallet": wallet,
            "condition_id": "ALL",
            "granularity": "1d",
            "start_time": (asof - timedelta(days=_PNL_LOOKBACK_DAYS)).date().isoformat(),
            "end_time": asof.date().isoformat(),
        }
        raw = self._soft("trader_pnl", params, default=None)
        return [TraderPnlPoint.model_validate(row) for row in _results(raw)] if raw else []

    def get_trader_trades(self, wallet: str, since: datetime) -> list[FalconTrade]:
        params = {
            "proxy_wallet": wallet,
            "condition_id": "ALL",
            "start_time": str(int(since.timestamp())),
        }
        raw = self._soft("trader_trades", params, default=None)
        return [FalconTrade.model_validate(row) for row in _results(raw)] if raw else []

    def get_market(self, condition_id: str) -> FalconMarket | None:
        raw = self._soft("market", {"condition_id": condition_id}, default=None)
        rows = _results(raw) if raw else []
        return FalconMarket.model_validate(rows[0]) if rows else None

    def get_orderbook(self, token_id: str) -> Orderbook | None:
        raw = self._soft("orderbook", {"token_id": token_id}, default=None)
        return Orderbook.model_validate(raw) if raw else None

    def get_open_positions(self, wallet: str) -> list[OpenPosition]:
        params = {"proxy_wallet": wallet, "condition_id": "ALL"}
        raw = self._soft("open_positions", params, default=None)
        return [OpenPosition.model_validate(row) for row in _results(raw)] if raw else []


# agent_id per dataset for the live gateway; top-traders confirmed (579), the rest pending the
# reachability check (§3.1). The live path is only built when a token + USE_LIVE_VENUES are set.
_FALCON_AGENT_IDS: dict[FalconDataset, int] = {"top_traders": 579}


def make_falcon_client(
    *,
    base_url: str | None,
    token: str | None,
    use_live: bool,
    fixtures_dir: Path,
    agent_ids: Mapping[FalconDataset, int] | None = None,
) -> FalconClient:
    """Live transport only when explicitly enabled and credentialed; otherwise offline fixtures."""
    if use_live and token and base_url:
        return FalconClient(HttpxFalconTransport(base_url, token, agent_ids or _FALCON_AGENT_IDS))
    return FalconClient(FixtureFalconTransport(fixtures_dir))
