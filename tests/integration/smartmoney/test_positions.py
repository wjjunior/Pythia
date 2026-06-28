"""Integration tests (PYT-157): current-positions storage (replace-per-poll) + the worker sweep."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import HeldPosition, TrackedTrader, TraderState
from pythia.db.smartmoney import TrackedTraderRepo, TraderPositionRepo
from pythia.ingestion.market_crawler import LiveVenueAccessError
from pythia.worker.smartmoney_ingest import run_positions_sweep

pytestmark = pytest.mark.integration

_TS = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _trader(session: Session, *, wallet: str, state: TraderState = TraderState.ACTIVE) -> str:
    trader = TrackedTraderRepo(session).add(
        TrackedTrader(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, wallet)),
            wallet=wallet,
            state=state,
            added_at=_TS,
            state_since=_TS,
        )
    )
    return trader.id


def _held(condition_id: str, value: float, *, outcome: str = "Yes") -> HeldPosition:
    return HeldPosition(
        condition_id=condition_id,
        outcome=outcome,
        size=100.0,
        avg_price=0.4,
        cur_price=0.5,
        current_value=value,
        cash_pnl=10.0,
        title=f"market {condition_id}",
        end_date=_TS,
    )


class _FakePositions:
    def __init__(self, by_wallet: dict[str, list[HeldPosition]]) -> None:
        self._by = by_wallet

    def current_positions(self, wallet: str) -> list[HeldPosition]:
        return self._by.get(wallet, [])


def test_replace_for_trader_stores_positions_ordered_by_value(session: Session) -> None:
    trader_id = _trader(session, wallet="0xa")
    repo = TraderPositionRepo(session)

    repo.replace_for_trader(trader_id, [_held("0xc1", 100.0), _held("0xc2", 300.0)], asof=_TS)

    stored = repo.list_for_trader(trader_id)
    assert [p.condition_id for p in stored] == ["0xc2", "0xc1"]  # current_value desc
    assert stored[0].current_value == 300.0 and stored[0].trader_id == trader_id


def test_replace_for_trader_is_wholesale_no_stale_carryover(session: Session) -> None:
    trader_id = _trader(session, wallet="0xb")
    repo = TraderPositionRepo(session)
    repo.replace_for_trader(trader_id, [_held("0xc1", 100.0), _held("0xc2", 200.0)], asof=_TS)

    # A later poll holds only one market — the closed one must disappear, not linger.
    repo.replace_for_trader(trader_id, [_held("0xc2", 250.0)], asof=_TS)

    stored = repo.list_for_trader(trader_id)
    assert [p.condition_id for p in stored] == ["0xc2"]
    assert stored[0].current_value == 250.0  # the surviving position reflects the fresh poll


def test_positions_sweep_replaces_non_retired_wallets_only(session: Session) -> None:
    active = _trader(session, wallet="0xactive", state=TraderState.ACTIVE)
    retired = _trader(session, wallet="0xretired", state=TraderState.RETIRED)
    positions = _FakePositions(
        {"0xactive": [_held("0xc1", 100.0)], "0xretired": [_held("0xc9", 999.0)]}
    )

    run_positions_sweep(session, positions, now=_TS)

    repo = TraderPositionRepo(session)
    assert [p.condition_id for p in repo.list_for_trader(active)] == ["0xc1"]
    assert repo.list_for_trader(retired) == []  # retired wallets are never swept


def test_positions_sweep_surfaces_a_geo_block_without_crashing(session: Session) -> None:
    _trader(session, wallet="0xgeo")

    class _GeoBlocked:
        def current_positions(self, wallet: str) -> list[HeldPosition]:
            raise LiveVenueAccessError("geo")

    run_positions_sweep(session, _GeoBlocked(), now=_TS)  # §8.6: logged, returns, never raises
