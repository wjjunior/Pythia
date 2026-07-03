"""Paper portfolio (§A.1): positions + capital, recomputed from the ledger (§4.4.1). PYT-181 adds
market metadata (Polymarket link + a stale flag), a resolved history, and a manual void."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from pythia.api.deps import BroadcasterDep, SessionDep, SettingsDep
from pythia.api.events import SseChannel
from pythia.api.schemas import CapitalState, ChannelPnl, PaperPortfolio, PaperPositionView
from pythia.common import Market, PaperTrade, TradeStatus
from pythia.db.repositories import PaperTradeRepository
from pythia.paper import NotOpenError, PaperLedger, UnknownTradeError
from pythia.portfolio import exposure_fraction
from pythia.services.portfolio import PortfolioService

router = APIRouter(tags=["paper"])


def _view(trade: PaperTrade, market: Market, *, now: datetime) -> PaperPositionView:
    closed = market.close_time is not None and market.close_time < now
    return PaperPositionView(
        trade=trade,
        question=market.question,
        event_slug=market.event_slug,
        market_resolved=market.resolved,
        market_closed=closed and not market.resolved,
    )


@router.get("/api/paper/positions")
def paper_positions(session: SessionDep, settings: SettingsDep) -> PaperPortfolio:
    now = datetime.now(UTC)
    repo = PaperTradeRepository(session)
    rows = repo.list_with_market()
    views = [_view(trade, market, now=now) for trade, market in rows]
    capital = PortfolioService(session, settings).capital()
    channel_rows = repo.pnl_by_channel()
    by_channel = {
        channel: ChannelPnl(
            realized_pnl=realized,
            locked_in_open=locked,
            n_open=n_open,
            n_resolved=n_resolved,
        )
        for channel, (realized, locked, n_open, n_resolved) in channel_rows.items()
    }
    return PaperPortfolio(
        positions=[v for v in views if v.trade.status is TradeStatus.OPEN],
        resolved=[v for v in views if v.trade.status is not TradeStatus.OPEN],
        capital=CapitalState(
            starting_bankroll=capital.starting_bankroll,
            realized_pnl=capital.realized_pnl,
            locked_in_open=capital.locked_in_open,
            available_capital=capital.available_capital,
            exposure_fraction=exposure_fraction(capital.locked_in_open, capital.total_capital),
        ),
        by_channel=by_channel,
    )


@router.post("/api/paper/positions/{trade_id}/close")
def close_position(
    trade_id: uuid.UUID, session: SessionDep, broadcaster: BroadcasterDep
) -> PaperTrade:
    try:
        trade = PaperLedger(session).void(str(trade_id), datetime.now(UTC))
    except UnknownTradeError:
        raise HTTPException(status_code=404, detail="position not found") from None
    except NotOpenError:
        raise HTTPException(status_code=409, detail="position is not open") from None
    broadcaster.publish(SseChannel.PAPER_CHANGED, trade.id)
    return trade
