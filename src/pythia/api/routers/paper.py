"""Paper portfolio (§A.1): open positions + capital state recomputed from the ledger (§4.4.1)."""

from fastapi import APIRouter

from pythia.api.deps import SessionDep, SettingsDep
from pythia.api.schemas import CapitalState, PaperPortfolio
from pythia.db.repositories import PaperTradeRepository
from pythia.portfolio import available_capital, exposure_fraction

router = APIRouter(tags=["paper"])


@router.get("/api/paper/positions")
def paper_positions(session: SessionDep, settings: SettingsDep) -> PaperPortfolio:
    repo = PaperTradeRepository(session)
    realized = repo.realized_pnl()
    locked = repo.locked_in_open()
    total_capital = settings.bankroll_paper + realized
    return PaperPortfolio(
        positions=repo.list_open(),
        capital=CapitalState(
            starting_bankroll=settings.bankroll_paper,
            realized_pnl=realized,
            locked_in_open=locked,
            available_capital=available_capital(settings.bankroll_paper, realized, locked),
            exposure_fraction=exposure_fraction(locked, total_capital),
        ),
    )
