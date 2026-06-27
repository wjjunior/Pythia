"""Derive a wallet's net open positions from its trade history (v2 §5.5, PYT-132).

Falcon exposes no positions endpoint, so an open position is reconstructed by netting a wallet's
BUYs against its SELLs per ``(condition_id, outcome)``. Strictly point-in-time: only trades at or
before ``asof`` count (§3.5, AC-S3). A position is emitted only when net size > 0, so a bounded
trade window can miss an old position but never fabricates one — the calibration-safe direction.
"""

from collections.abc import Sequence
from datetime import datetime

from pythia.ingestion.falcon.models import FalconTrade, OpenPosition

_SIDE_BUY = "BUY"
_SIDE_SELL = "SELL"


def derive_open_positions(trades: Sequence[FalconTrade], asof: datetime) -> list[OpenPosition]:
    bought: dict[tuple[str, str], float] = {}
    sold: dict[tuple[str, str], float] = {}
    buy_cost: dict[tuple[str, str], float] = {}  # sum(price*size) over buys, for the entry price
    for trade in trades:
        if trade.ts > asof:
            continue  # point-in-time: a trade after asof cannot inform the position (AC-S3)
        key = (trade.condition_id, trade.outcome)
        side = trade.side.upper()
        if side == _SIDE_BUY:
            bought[key] = bought.get(key, 0.0) + trade.size
            buy_cost[key] = buy_cost.get(key, 0.0) + trade.price * trade.size
        elif side == _SIDE_SELL:
            sold[key] = sold.get(key, 0.0) + trade.size

    positions: list[OpenPosition] = []
    for key in sorted(bought):  # only a buy can open a position; sorted ⇒ reproducible order
        net = bought[key] - sold.get(key, 0.0)
        if net <= 0.0:
            continue  # fully closed or net-flat — not an open position
        condition_id, outcome = key
        avg_price = buy_cost[key] / bought[key]  # size-weighted entry, context-only (§5.5)
        positions.append(
            OpenPosition(condition_id=condition_id, outcome=outcome, size=net, avg_price=avg_price)
        )
    return positions
