"""Growth simulation (§A.1, §4.8): seeded Monte Carlo projection from MEASURED calibration only.

Honesty rule: the only input is `CalibrationService.records()` — the measured per-category
(p_model, q, outcome) samples. There is no path that simulates from an assumed edge. Deterministic
given `GROWTH_SIM_SEED`.
"""

from fastapi import APIRouter, HTTPException

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import GrowthCurvePointView, SimulationResponse
from pythia.eval.service import CalibrationService
from pythia.eval.simulation.montecarlo import simulate
from pythia.portfolio.service import PortfolioService

router = APIRouter(tags=["simulation"])


@router.get("/api/simulation")
def simulation(
    session: SessionDep, settings: SettingsDep, category: str | None = None
) -> SimulationResponse:
    records = CalibrationService(session).records()
    samples = (
        records.get(category, [])
        if category is not None
        else [sample for group in records.values() for sample in group]
    )
    if not samples:
        raise HTTPException(status_code=404, detail="no resolved measurements to simulate from")

    capital = PortfolioService(session, settings).capital()
    result = simulate(
        samples,
        starting_bankroll=capital.total_capital,
        kelly_mult=settings.kelly_mult,
        cap_per_trade=settings.cap_per_trade,
        ruin_floor=settings.ruin_floor,
        paths=settings.growth_sim_paths,
        horizon=settings.growth_sim_horizon,
        seed=settings.growth_sim_seed,
    )
    return SimulationResponse(
        category=category,
        n=result.n,
        paths=result.paths,
        horizon=result.horizon,
        starting_bankroll=result.starting_bankroll,
        terminal_p5=result.terminal_p5,
        terminal_p50=result.terminal_p50,
        terminal_p95=result.terminal_p95,
        log_growth_rate=result.log_growth_rate,
        mean_max_drawdown=result.mean_max_drawdown,
        p95_max_drawdown=result.p95_max_drawdown,
        probability_of_ruin=result.probability_of_ruin,
        ruin_floor=settings.ruin_floor,
        growth_curve=[
            GrowthCurvePointView(kelly_mult=point.kelly_mult, log_growth_rate=point.log_growth_rate)
            for point in result.growth_curve
        ],
    )
