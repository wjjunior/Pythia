"""Property/behaviour tests for the pure growth simulation (§4.8)."""

import math

import pytest

from pythia.eval.simulation.models import SimulationResult
from pythia.eval.simulation.montecarlo import DEFAULT_CURVE_MULTS, simulate

_BANKROLL = 10_000.0
_EDGE_SAMPLES = [
    (0.7, 0.5, 1),
    (0.7, 0.5, 1),
    (0.7, 0.5, 0),
    (0.4, 0.5, 0),
]


def _run(samples: list[tuple[float, float, int]], **kwargs: float) -> SimulationResult:
    params: dict[str, float] = {
        "starting_bankroll": _BANKROLL,
        "kelly_mult": 0.25,
        "cap_per_trade": 0.02,
        "ruin_floor": 0.5,
        "paths": 2_000,
        "horizon": 100,
        "seed": 42,
    }
    params.update(kwargs)
    return simulate(samples, **params)  # type: ignore[arg-type]


def test_same_seed_is_reproducible() -> None:
    first = _run(_EDGE_SAMPLES)
    second = _run(_EDGE_SAMPLES)
    assert first == second


def test_different_seed_changes_the_paths() -> None:
    assert _run(_EDGE_SAMPLES, seed=1).terminal_p50 != _run(_EDGE_SAMPLES, seed=2).terminal_p50


def test_empty_samples_is_rejected() -> None:
    with pytest.raises(ValueError, match="measured samples"):
        _run([])


def test_reports_the_sample_count() -> None:
    assert _run(_EDGE_SAMPLES).n == len(_EDGE_SAMPLES)


def test_percentiles_are_ordered() -> None:
    result = _run(_EDGE_SAMPLES)
    assert result.terminal_p5 <= result.terminal_p50 <= result.terminal_p95


def test_probability_of_ruin_is_a_unit_fraction() -> None:
    assert 0.0 <= _run(_EDGE_SAMPLES).probability_of_ruin <= 1.0


def test_drawdown_is_a_unit_fraction() -> None:
    result = _run(_EDGE_SAMPLES)
    assert 0.0 <= result.mean_max_drawdown <= result.p95_max_drawdown <= 1.0


def test_positive_edge_distribution_grows() -> None:
    assert _run(_EDGE_SAMPLES).log_growth_rate > 0.0


def test_negative_edge_distribution_shrinks() -> None:
    anti_calibrated = [(0.7, 0.5, 0), (0.7, 0.5, 0), (0.7, 0.5, 0), (0.7, 0.5, 1)]
    assert _run(anti_calibrated).log_growth_rate < 0.0


def test_growth_curve_covers_the_kelly_grid() -> None:
    result = _run(_EDGE_SAMPLES)
    assert tuple(point.kelly_mult for point in result.growth_curve) == DEFAULT_CURVE_MULTS


def test_log_growth_rate_matches_curve_at_configured_mult() -> None:
    result = _run(_EDGE_SAMPLES, kelly_mult=0.5)
    at_half = next(p for p in result.growth_curve if p.kelly_mult == 0.5)
    assert math.isclose(result.log_growth_rate, at_half.log_growth_rate)


def test_no_bet_when_edge_is_flat_keeps_bankroll() -> None:
    flat = [(0.5, 0.5, 1), (0.5, 0.5, 0)]
    result = _run(flat, paths=500, horizon=50)
    assert result.log_growth_rate == 0.0
    assert result.terminal_p50 == pytest.approx(_BANKROLL)
    assert result.probability_of_ruin == 0.0


@pytest.mark.parametrize(
    "bad",
    [
        {"paths": 0},
        {"horizon": 0},
        {"starting_bankroll": 0.0},
        {"starting_bankroll": -1.0},
        {"cap_per_trade": 1.0},
        {"cap_per_trade": -0.1},
    ],
)
def test_rejects_out_of_range_parameters(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        _run(_EDGE_SAMPLES, **bad)


def test_drawdown_includes_the_starting_bankroll_as_peak() -> None:
    first_step_loss = [(0.7, 0.5, 0)]
    result = _run(first_step_loss, cap_per_trade=0.02, paths=5, horizon=1)
    assert result.mean_max_drawdown == pytest.approx(0.02)


def test_caps_the_per_trade_fraction() -> None:
    huge_edge = [(0.99, 0.5, 1)]
    capped = _run(huge_edge, cap_per_trade=0.01, paths=10, horizon=1)
    expected = _BANKROLL * (1.0 + 0.01 * (1.0 - 0.5) / 0.5)
    assert capped.terminal_p50 == pytest.approx(expected)
