"""Portfolio (§A.1, §4.4.1): live capital state + exposure recomputed from the ledger."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import CapitalState, ExposureView, PortfolioResponse
from pythia.portfolio import exposure_fraction
from pythia.portfolio.service import PortfolioService

router = APIRouter(tags=["portfolio"])


@router.get("/api/portfolio")
def portfolio(session: SessionDep, settings: SettingsDep) -> PortfolioResponse:
    service = PortfolioService(session, settings)
    realized = service.realized_pnl()
    locked = service.locked_in_open()
    exposure = service.exposure_state()
    total_capital = settings.bankroll_paper + realized
    return PortfolioResponse(
        capital=CapitalState(
            starting_bankroll=settings.bankroll_paper,
            realized_pnl=realized,
            locked_in_open=locked,
            available_capital=service.available_capital(),
            exposure_fraction=exposure_fraction(locked, total_capital),
        ),
        exposure=ExposureView(
            per_category=exposure.per_category,
            per_correlation_group=exposure.per_group,
            daily_total=exposure.daily_total,
            weekly_total=exposure.weekly_total,
            absolute_total=exposure.absolute_total,
        ),
    )
