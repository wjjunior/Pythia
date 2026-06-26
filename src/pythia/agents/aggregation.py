"""Pure forecast aggregation (§5.4): combine diverse member estimates into one p_model.

No I/O, no clock — aggregation is deterministic given the member list (a §4.6-style invariant).
The production ensemble path uses ``median`` (it cancels an outlier member). The ``weighted`` method
is an explicit-weight mean kept for the harness and a future calibration-weighting wiring; it is
**unwired in production (PYT-125)** — no caller derives per-model weights (there is no per-model
Brier track record to weight on yet), so it is *not* calibration-weighted today and the live verdict
is always the median.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from pythia.common import Estimate
from pythia.config.settings import AggregationMethod


@dataclass(frozen=True, slots=True)
class AggregatedForecast:
    p_model: float
    confidence: float
    lower: float
    upper: float


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    if total <= 0.0:
        return statistics.fmean(values)
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / total


def _combine(values: Sequence[float], method: AggregationMethod, weights: Sequence[float]) -> float:
    if method == "weighted":
        return _weighted_mean(values, weights)
    return statistics.median(values)


def aggregate(
    members: Sequence[Estimate],
    method: AggregationMethod = "median",
    *,
    weights: Sequence[float] | None = None,
) -> AggregatedForecast:
    if not members:
        raise ValueError("cannot aggregate an empty ensemble")
    if weights is not None and len(weights) != len(members):
        raise ValueError("weights length must match the number of members")
    w = list(weights) if weights is not None else [1.0] * len(members)
    return AggregatedForecast(
        p_model=_combine([m.p_model for m in members], method, w),
        confidence=_combine([m.confidence for m in members], method, w),
        lower=statistics.median([m.lower for m in members]),
        upper=statistics.median([m.upper for m in members]),
    )
