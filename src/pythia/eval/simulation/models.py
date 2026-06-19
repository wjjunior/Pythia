"""Value objects for the growth simulation (§4.8). Frozen, framework-free."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GrowthCurvePoint:
    kelly_mult: float
    log_growth_rate: float


@dataclass(frozen=True, slots=True)
class SimulationResult:
    n: int
    paths: int
    horizon: int
    starting_bankroll: float
    terminal_p5: float
    terminal_p50: float
    terminal_p95: float
    log_growth_rate: float
    mean_max_drawdown: float
    p95_max_drawdown: float
    probability_of_ruin: float
    growth_curve: list[GrowthCurvePoint]
