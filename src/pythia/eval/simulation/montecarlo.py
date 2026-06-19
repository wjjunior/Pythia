"""Growth simulation (§4.8): Monte Carlo Kelly bankroll projection from the MEASURED distribution.

A positive per-trade edge does not guarantee survival — variance and drawdown can ruin a mis-sized
positive-edge strategy. This projects the bankroll forward by bootstrap-replaying the measured
(p_model, q, outcome) opportunities, sized by fractional Kelly. Honesty rule: the only edge source
is the measured samples — there is no path that simulates from an assumed edge. Deterministic given
the seed; randomness enters only as that parameter.
"""

import numpy as np
from numpy.typing import NDArray

from pythia.eval.simulation.models import GrowthCurvePoint, SimulationResult

DEFAULT_CURVE_MULTS = (0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
_EPS = 1e-9


def _trade_multipliers(
    samples: NDArray[np.float64], kelly_mult: float, cap_per_trade: float
) -> NDArray[np.float64]:
    p_model = samples[:, 0]
    q = np.clip(samples[:, 1], _EPS, 1.0 - _EPS)
    outcome = samples[:, 2]
    edge = p_model - q
    bet_yes = edge > 0.0
    entry = np.where(bet_yes, q, 1.0 - q)
    kelly = np.where(edge == 0.0, 0.0, np.where(bet_yes, edge / (1.0 - q), -edge / q))
    won = np.where(bet_yes, outcome >= 0.5, outcome < 0.5)
    bet = np.clip(kelly * kelly_mult, 0.0, cap_per_trade)
    multipliers: NDArray[np.float64] = np.where(won, 1.0 + bet * (1.0 - entry) / entry, 1.0 - bet)
    return multipliers


def _expected_log_growth(multipliers: NDArray[np.float64]) -> float:
    return float(np.mean(np.log(multipliers)))


def simulate(
    samples: list[tuple[float, float, int]],
    *,
    starting_bankroll: float,
    kelly_mult: float,
    cap_per_trade: float,
    ruin_floor: float,
    paths: int,
    horizon: int,
    seed: int,
    curve_mults: tuple[float, ...] = DEFAULT_CURVE_MULTS,
) -> SimulationResult:
    if not samples:
        raise ValueError("growth simulation requires measured samples; none were provided")
    if paths <= 0 or horizon <= 0:
        raise ValueError("paths and horizon must be positive")
    if starting_bankroll <= 0.0:
        raise ValueError("starting_bankroll must be positive")
    if not 0.0 <= cap_per_trade < 1.0:
        raise ValueError("cap_per_trade must be in [0, 1)")

    data = np.array(samples, dtype=np.float64)
    multipliers = _trade_multipliers(data, kelly_mult, cap_per_trade)

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(samples), size=(paths, horizon))
    growth = np.cumprod(multipliers[draws], axis=1)
    start_column = np.ones((paths, 1), dtype=np.float64)
    wealth = starting_bankroll * np.concatenate([start_column, growth], axis=1)
    terminal = wealth[:, -1]
    p5, p50, p95 = np.percentile(terminal, [5.0, 50.0, 95.0])

    running_max = np.maximum.accumulate(wealth, axis=1)
    max_drawdown = ((running_max - wealth) / running_max).max(axis=1)
    ruined = (wealth < ruin_floor * starting_bankroll).any(axis=1)

    return SimulationResult(
        n=len(samples),
        paths=paths,
        horizon=horizon,
        starting_bankroll=starting_bankroll,
        terminal_p5=float(p5),
        terminal_p50=float(p50),
        terminal_p95=float(p95),
        log_growth_rate=_expected_log_growth(multipliers),
        mean_max_drawdown=float(np.mean(max_drawdown)),
        p95_max_drawdown=float(np.percentile(max_drawdown, 95.0)),
        probability_of_ruin=float(np.mean(ruined)),
        growth_curve=[
            GrowthCurvePoint(
                kelly_mult=mult,
                log_growth_rate=_expected_log_growth(_trade_multipliers(data, mult, cap_per_trade)),
            )
            for mult in curve_mults
        ],
    )
