"""Value objects for the calibration metrics (§4.6, §5.2). Frozen, framework-free."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_forecast: float
    observed_frequency: float


@dataclass(frozen=True, slots=True)
class CategoryCalibration:
    category: str
    brier_system: float
    brier_market: float
    delta: float
    n: int
    invalid_count: int = 0
    invalid_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class EvCalibration:
    mean_predicted: float
    mean_realized: float
    n: int


@dataclass(frozen=True, slots=True)
class RecalibrationReport:
    brier_raw: float
    brier_recalibrated: float
    n: int
