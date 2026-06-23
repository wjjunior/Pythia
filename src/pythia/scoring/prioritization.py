"""Calibration-priority helpers: bet side, entry price, win multiple, days-to-close.

Pure (no clock, no I/O): time enters as the `asof` parameter. Used to rank the radar toward
near-term, high-payout options so resolutions — and calibration data — arrive quickly.
"""

from dataclasses import dataclass
from datetime import datetime

from pythia.common import Side

_SECONDS_PER_DAY = 86400.0
# A degenerate 0/1 price would give an infinite payout; clamp so the value stays JSON-finite.
_MIN_ENTRY_PRICE = 1e-6


def recommended_side(p_model: float, q: float) -> Side:
    return Side.YES if p_model >= q else Side.NO


def entry_price(side: Side, q: float) -> float:
    return q if side is Side.YES else 1.0 - q


def win_multiple(entry: float) -> float:
    return 1.0 / max(entry, _MIN_ENTRY_PRICE)


def closes_in_days(close_time: datetime | None, asof: datetime) -> float | None:
    if close_time is None:
        return None
    return (close_time - asof).total_seconds() / _SECONDS_PER_DAY


@dataclass(frozen=True, slots=True)
class RadarMetrics:
    recommended_side: Side
    entry_price: float
    win_multiple: float
    closes_in_days: float | None


def radar_metrics(
    *, p_model: float, q: float, close_time: datetime | None, asof: datetime
) -> RadarMetrics:
    side = recommended_side(p_model, q)
    entry = entry_price(side, q)
    return RadarMetrics(
        recommended_side=side,
        entry_price=entry,
        win_multiple=win_multiple(entry),
        closes_in_days=closes_in_days(close_time, asof),
    )
