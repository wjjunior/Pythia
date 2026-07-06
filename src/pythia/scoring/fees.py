"""Polymarket taker-fee schedule (PYT-190): per-unit fee = rate(category) * p * (1-p), versioned by
effective date, point-in-time. Pure — the schedule and settings values are passed in, never read
inside; the composition roots supply them."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class FeeScheduleVersion:
    effective_from: datetime
    rates: dict[str, float]  # canonical fee family -> per-unit rate


# Polymarket's 2026 taker schedule. effective_from is a placeholder — set it to the real
# fee-introduction date so an asof before it is correctly fee-free (rate 0).
DEFAULT_FEE_SCHEDULE: tuple[FeeScheduleVersion, ...] = (
    FeeScheduleVersion(
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rates={
            "crypto": 0.07,
            "econ": 0.05,
            "culture": 0.05,
            "weather": 0.05,
            "finance": 0.04,
            "politics": 0.04,
            "tech": 0.04,
            "sports": 0.03,
            "geopolitics": 0.0,
        },
    ),
)

# DB category (lowercased) -> canonical family. An unlisted value stays as its lowercased self,
# which won't match a version's rates and so falls to the default rate.
_FAMILY_ALIASES: dict[str, str] = {
    "world affairs": "geopolitics",
    "geopolitics": "geopolitics",
    "movies": "culture",
    "culture": "culture",
    "crypto prices": "crypto",
    "crypto": "crypto",
    "technology": "tech",
    "tech": "tech",
    "economics": "econ",
    "economy": "econ",
    "econ": "econ",
    "finance": "finance",
    "politics": "politics",
    "sports": "sports",
    "weather": "weather",
}


def taker_fee(rate: float, p: float) -> float:
    return rate * p * (1.0 - p)


def fee_family(category: str) -> str:
    key = category.lower().strip()
    return _FAMILY_ALIASES.get(key, key)


def rate_asof(
    schedule: Sequence[FeeScheduleVersion], family: str, asof: datetime, default_rate: float
) -> float:
    effective = [v for v in schedule if v.effective_from <= asof]
    if not effective:
        return 0.0  # asof precedes every version — the pre-fee era
    version = max(effective, key=lambda v: v.effective_from)
    return version.rates.get(family, default_rate)


def taker_fee_for(
    schedule: Sequence[FeeScheduleVersion],
    category: str,
    p: float,
    asof: datetime,
    default_rate: float,
) -> float:
    return taker_fee(rate_asof(schedule, fee_family(category), asof, default_rate), p)


def taker_fee_fn(
    schedule: Sequence[FeeScheduleVersion], default_rate: float, flat_fee: float = 0.0
) -> Callable[[str, float, datetime], float]:
    def fee_fn(category: str, p: float, asof: datetime) -> float:
        return flat_fee + taker_fee_for(schedule, category, p, asof, default_rate)

    return fee_fn
