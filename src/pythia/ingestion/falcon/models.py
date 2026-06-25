"""Falcon API DTOs (v2 §4.5). Frozen value objects parsed at the adapter boundary.

Numeric fields arrive as JSON strings ("0.41", "1494121.71") — Pydantic coerces them. Aliases map
Falcon's wire names (``id``, ``proxy_wallet``, ``timestamp``) to clean field names.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _FalconModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class TopTrader(_FalconModel):
    address: str
    rank: int
    total_pnl: float
    total_trades: int
    win_rate: float
    markets_traded: int
    roi: float | None = None
    sharpe_ratio: float | None = None
    total_invested: float | None = None
    avg_trade_size: float | None = None


class TraderPnlPoint(_FalconModel):
    date: datetime
    invested: float
    pnl: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    wallet: str = Field(alias="proxy_wallet")


class FalconTrade(_FalconModel):
    trade_id: str = Field(alias="id")
    wallet: str = Field(alias="proxy_wallet")
    condition_id: str
    outcome: str
    side: str
    price: float
    size: float
    slug: str
    transaction_hash: str
    ts: datetime = Field(alias="timestamp")


class FalconMarket(_FalconModel):
    condition_id: str
    question: str
    slug: str
    closed: bool
    event_slug: str | None = None
    end_date: datetime | None = None
    volume_total: float | None = None
    winning_outcome: str | None = None
    side_a_outcome: str | None = None
    side_a_token_id: str | None = None
    side_b_outcome: str | None = None
    side_b_token_id: str | None = None


class OpenPosition(_FalconModel):
    condition_id: str
    outcome: str
    size: float
    # The trader's historical entry price — recorded for context, NEVER used to evaluate viability:
    # an open-position candidate is judged at the current price, asof = now (§5.5, AC-S8).
    avg_price: float | None = None


class OrderbookLevel(_FalconModel):
    price: float
    size: float


class Orderbook(_FalconModel):
    token_id: str
    ts: datetime = Field(alias="timestamp")
    bids: list[OrderbookLevel]
    asks: list[OrderbookLevel]
