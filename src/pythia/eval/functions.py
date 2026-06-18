"""Calibration metrics (§4.6 inv 3, §5.2): Brier, reliability bins, market comparison.

Pure: no clock, no randomness, no I/O. Structural preconditions (matching lengths, non-empty
inputs, positive bin counts) raise ValueError; value-domain validity (outcomes in {0, 1},
forecasts in [0, 1]) is assumed from the boundary.
"""

from collections.abc import Mapping, Sequence

from pythia.eval.models import CategoryCalibration, EvCalibration, ReliabilityBin


def brier(forecast: float, outcome: int) -> float:
    return (forecast - outcome) ** 2


def brier_score(forecasts: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(forecasts) != len(outcomes):
        raise ValueError("forecasts and outcomes must have the same length")
    if not forecasts:
        raise ValueError("forecasts and outcomes must be non-empty")
    pairs = zip(forecasts, outcomes, strict=True)
    return sum(brier(forecast, outcome) for forecast, outcome in pairs) / len(forecasts)


def reliability_bins(
    forecasts: Sequence[float], outcomes: Sequence[int], n_bins: int = 10
) -> list[ReliabilityBin]:
    if n_bins < 1:
        raise ValueError("n_bins must be a positive integer")
    if len(forecasts) != len(outcomes):
        raise ValueError("forecasts and outcomes must have the same length")

    members: list[list[int]] = [[] for _ in range(n_bins)]
    for index, forecast in enumerate(forecasts):
        members[min(int(forecast * n_bins), n_bins - 1)].append(index)

    width = 1.0 / n_bins
    bins: list[ReliabilityBin] = []
    for position, indices in enumerate(members):
        if not indices:
            continue
        binned_forecasts = [forecasts[i] for i in indices]
        binned_outcomes = [outcomes[i] for i in indices]
        bins.append(
            ReliabilityBin(
                lower=position * width,
                upper=(position + 1) * width,
                count=len(indices),
                mean_forecast=sum(binned_forecasts) / len(binned_forecasts),
                observed_frequency=sum(binned_outcomes) / len(binned_outcomes),
            )
        )
    return bins


def calibration_by_category(
    records: Mapping[str, Sequence[tuple[float, float, int]]],
) -> list[CategoryCalibration]:
    result: list[CategoryCalibration] = []
    for category, rows in records.items():
        system = [row[0] for row in rows]
        market = [row[1] for row in rows]
        outcomes = [row[2] for row in rows]
        brier_system = brier_score(system, outcomes)
        brier_market = brier_score(market, outcomes)
        result.append(
            CategoryCalibration(
                category=category,
                brier_system=brier_system,
                brier_market=brier_market,
                delta=brier_market - brier_system,
                n=len(rows),
            )
        )
    return result


def ev_predicted_vs_realized(
    predicted: Sequence[float], realized: Sequence[float]
) -> EvCalibration:
    count = len(predicted)
    if count != len(realized):
        raise ValueError("predicted and realized must have the same length")
    if count == 0:
        raise ValueError("predicted and realized must be non-empty")
    return EvCalibration(
        mean_predicted=sum(predicted) / count,
        mean_realized=sum(realized) / count,
        n=count,
    )
