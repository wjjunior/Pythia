"""Eval cohorts (v1 §4.11, AC-17): config-defined market slices for faster, honest calibration.

A cohort is a measurement LABEL, separate from any trade filter. `cohorts_for` is pure — the same
predicate tags a paper trade at the review gate and stratifies a calibration record, so tag and
measure always agree. `price` is the market YES price at `asof` (never the side-specific fill).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pythia.config import Settings


class Cohort(StrEnum):
    FAST_7D = "fast_7d"
    MIDBAND = "midband"
    OVERALL = "overall"


@dataclass(frozen=True, slots=True)
class CohortPolicy:
    max_horizon_days: float
    price_min: float
    price_max: float


def cohorts_for(
    *, price: float, horizon_days: float | None, policy: CohortPolicy
) -> frozenset[Cohort]:
    members: set[Cohort] = set()
    if horizon_days is not None and horizon_days <= policy.max_horizon_days:
        members.add(Cohort.FAST_7D)
    if policy.price_min <= price <= policy.price_max:
        members.add(Cohort.MIDBAND)
    return frozenset(members)


def cohort_policy(settings: Settings) -> CohortPolicy:
    return CohortPolicy(
        max_horizon_days=settings.eval_cohort_max_horizon_days,
        price_min=settings.eval_cohort_price_min,
        price_max=settings.eval_cohort_price_max,
    )
