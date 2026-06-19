"""Pure capital-state math (§4.4.1). Available capital is recomputed from the ledger, never static.

No clock, no I/O — a thin service reads the ledger and supplies realized PnL, locked capital and the
open positions; time enters as the `asof` parameter to `exposure_state`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pythia.risk import ExposureState


@dataclass(frozen=True, slots=True)
class OpenPosition:
    size: float
    category: str
    correlation_group: str
    opened_at: datetime


def available_capital(
    starting_bankroll: float, realized_pnl: float, locked_in_open: float
) -> float:
    return starting_bankroll + realized_pnl - locked_in_open


def exposure_fraction(deployed: float, capital: float) -> float:
    return deployed / capital if capital > 0.0 else 1.0


def exposure_state(
    positions: list[OpenPosition],
    *,
    asof: datetime,
    daily_window: timedelta,
    weekly_window: timedelta,
) -> ExposureState:
    per_category: dict[str, float] = {}
    per_group: dict[str, float] = {}
    daily_total = 0.0
    weekly_total = 0.0
    absolute_total = 0.0
    for position in positions:
        per_category[position.category] = per_category.get(position.category, 0.0) + position.size
        per_group[position.correlation_group] = (
            per_group.get(position.correlation_group, 0.0) + position.size
        )
        absolute_total += position.size
        age = asof - position.opened_at
        if age <= daily_window:
            daily_total += position.size
        if age <= weekly_window:
            weekly_total += position.size
    return ExposureState(
        per_group=per_group,
        per_category=per_category,
        daily_total=daily_total,
        weekly_total=weekly_total,
        absolute_total=absolute_total,
    )
