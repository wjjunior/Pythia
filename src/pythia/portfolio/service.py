"""Capital-state service (§4.4.1): reads the paper ledger to supply live capital and exposure.

The impure companion to the pure portfolio math — it feeds available_capital and the current
ExposureState into the pipeline's sizing (§4.2) and risk caps (§4.4).
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

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


class PortfolioService:
    def __init__(
        self, session: Session, settings: Settings, *, now: Callable[[], datetime] = _utcnow
    ) -> None:
        self._trades = PaperTradeRepository(session)
        self._settings = settings
        self._now = now

    def realized_pnl(self) -> float:
        return self._trades.realized_pnl()

    def total_capital(self) -> float:
        return self._settings.bankroll_paper + self._trades.realized_pnl()

    def locked_in_open(self) -> float:
        return self._trades.locked_in_open()

    def available_capital(self) -> float:
        return _available_capital(
            self._settings.bankroll_paper,
            self._trades.realized_pnl(),
            self._trades.locked_in_open(),
        )

    def exposure_state(self) -> ExposureState:
        positions = [
            OpenPosition(
                size=size, category=category, correlation_group=category, opened_at=opened_at
            )
            for size, category, opened_at in self._trades.open_positions()
        ]
        return _exposure_state(
            positions,
            asof=self._now(),
            daily_window=_DAILY_WINDOW,
            weekly_window=_WEEKLY_WINDOW,
        )
