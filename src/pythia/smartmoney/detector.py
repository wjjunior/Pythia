"""New-entry detection (v2 §5.1, §4.6): a tracked wallet's trades → new/increased entries.

Each BUY is a new or increased position (``is_increase = True``); a SELL is an exit/decrease — both
are recorded as ``trader_entries`` but only increases are returned as candidate triggers (§4.6). The
write is idempotent on ``(trader_id, external_market, ts)`` so re-polling never duplicates an entry
or re-fires a candidate (AC-S1). Several fills at the same timestamp collapse to one entry.
"""

from pythia.common import TrackedTrader, TraderEntry
from pythia.common.ids import deterministic_id
from pythia.common.logging import get_logger
from pythia.db.smartmoney import TraderEntryRepo
from pythia.ingestion.falcon import FalconTrade

_BUY = "BUY"

_log = get_logger(__name__)


def _to_entry(trader: TrackedTrader, trade: FalconTrade) -> TraderEntry:
    return TraderEntry(
        id=deterministic_id(trader.id, trade.condition_id, trade.ts.isoformat()),
        trader_id=trader.id,
        external_market=trade.condition_id,
        side=trade.outcome,
        ts=trade.ts,
        is_increase=trade.side.upper() == _BUY,
        size_usd=trade.size,
        entry_price=trade.price,
    )


def detect_new_entries(
    trader: TrackedTrader, trades: list[FalconTrade], entry_repo: TraderEntryRepo
) -> list[TraderEntry]:
    """Persist a wallet's never-seen trades; return only genuinely new *increases* to trigger."""
    triggers: list[TraderEntry] = []
    for trade in trades:
        created = entry_repo.add_new(_to_entry(trader, trade))
        if created is not None and created.is_increase:
            triggers.append(created)
    if triggers:
        _log.info("new_entries_detected", wallet=trader.wallet, count=len(triggers))
    return triggers
