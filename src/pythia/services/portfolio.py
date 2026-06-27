"""Capital-state service (§4.4.1): reads the paper ledger to supply live capital and exposure.

The impure companion to the pure portfolio math — it feeds available_capital and the current
ExposureState into the pipeline's sizing (§4.2) and risk caps (§4.4). A position's correlation group
is its market, so per-market caps bind per market rather than collapsing onto the category.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from pythia.common import Side
from pythia.config import Settings
from pythia.db.repositories import PaperTradeRepository
from pythia.portfolio.functions import OpenPosition
from pythia.portfolio.functions import available_capital as _available_capital
from pythia.portfolio.functions import exposure_state as _exposure_state
from pythia.risk import ExposureState

_DAILY_WINDOW = timedelta(days=1)
_WEEKLY_WINDOW = timedelta(days=7)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CapitalSnapshot:
    starting_bankroll: float
    realized_pnl: float
    locked_in_open: float
    total_capital: float
    available_capital: float


class PortfolioService:
    def __init__(
        self, session: Session, settings: Settings, *, now: Callable[[], datetime] = _utcnow
    ) -> None:
        self._trades = PaperTradeRepository(session)
        self._settings = settings
        self._now = now

    def capital(self) -> CapitalSnapshot:
        bankroll = self._settings.bankroll_paper
        realized = self._trades.realized_pnl()
        locked = self._trades.locked_in_open()
        return CapitalSnapshot(
            starting_bankroll=bankroll,
            realized_pnl=realized,
            locked_in_open=locked,
            total_capital=bankroll + realized,
            available_capital=_available_capital(bankroll, realized, locked),
        )

    def exposure_state(self) -> ExposureState:
        positions = [
            OpenPosition(
                size=size,
                category=category,
                correlation_group=market_id,
                opened_at=opened_at,
                event_slug=event_slug,
                side=Side(side),
            )
            for size, category, market_id, opened_at, event_slug, side in (
                self._trades.open_positions()
            )
        ]
        return _exposure_state(
            positions,
            asof=self._now(),
            daily_window=_DAILY_WINDOW,
            weekly_window=_WEEKLY_WINDOW,
        )
