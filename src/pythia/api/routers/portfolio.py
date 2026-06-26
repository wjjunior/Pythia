"""Portfolio (§A.1, §4.4.1): live capital state + exposure recomputed from the ledger."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import CapitalState, ExposureView, PortfolioResponse
from pythia.portfolio import exposure_fraction
from pythia.services.portfolio import PortfolioService

router = APIRouter(tags=["portfolio"])


@router.get("/api/portfolio")
def portfolio(session: SessionDep, settings: SettingsDep) -> PortfolioResponse:
    service = PortfolioService(session, settings)
    capital = service.capital()
    exposure = service.exposure_state()
    return PortfolioResponse(
        capital=CapitalState(
            starting_bankroll=capital.starting_bankroll,
            realized_pnl=capital.realized_pnl,
            locked_in_open=capital.locked_in_open,
            available_capital=capital.available_capital,
            exposure_fraction=exposure_fraction(capital.locked_in_open, capital.total_capital),
        ),
        exposure=ExposureView(
            per_category=exposure.per_category,
            per_correlation_group=exposure.per_group,
            daily_total=exposure.daily_total,
            weekly_total=exposure.weekly_total,
            absolute_total=exposure.absolute_total,
        ),
    )
