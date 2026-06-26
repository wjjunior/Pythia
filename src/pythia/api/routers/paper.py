"""Paper portfolio (§A.1): open positions + capital state recomputed from the ledger (§4.4.1)."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import CapitalState, PaperPortfolio
from pythia.db.repositories import PaperTradeRepository
from pythia.portfolio import exposure_fraction
from pythia.services.portfolio import PortfolioService

router = APIRouter(tags=["paper"])


@router.get("/api/paper/positions")
def paper_positions(session: SessionDep, settings: SettingsDep) -> PaperPortfolio:
    capital = PortfolioService(session, settings).capital()
    return PaperPortfolio(
        positions=PaperTradeRepository(session).list_open(),
        capital=CapitalState(
            starting_bankroll=capital.starting_bankroll,
            realized_pnl=capital.realized_pnl,
            locked_in_open=capital.locked_in_open,
            available_capital=capital.available_capital,
            exposure_fraction=exposure_fraction(capital.locked_in_open, capital.total_capital),
        ),
    )
