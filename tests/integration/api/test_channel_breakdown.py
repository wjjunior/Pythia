"""Task 8: per-channel PnL and Brier breakdowns on /api/paper and /api/calibration (PYT-188).

Seeds resolved trades across radar/smartmoney/arb and resolved estimates across channels, then
asserts the additive by_channel fields are present and correct. Existing contract fields must
survive unchanged (no fields removed or renamed).
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from pythia.api.app import create_app
from pythia.api.deps import get_session, settings_dependency
from pythia.common import (
    Estimate,
    Market,
    MarketSnapshot,
    PaperTrade,
    Side,
    SignalSource,
    TradeStatus,
)
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.paper import PaperLedger

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_RESOLVE_AT = _TS + timedelta(days=10)


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


@pytest.fixture
def app(session: Session) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[settings_dependency] = lambda: Settings(
        _env_file=None, database_url="postgresql+psycopg://x/y"
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _seed_market(session: Session, key: str, category: str = "politics") -> tuple[str, str, str]:
    """Seed market + snapshot + estimate; return (market_id, snapshot_id, estimate_id)."""
    market_id = _uid(f"ch-{key}-mkt")
    snapshot_id = _uid(f"ch-{key}-snap")
    estimate_id = _uid(f"ch-{key}-est")
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"ch-{key}",
            question=f"Question {key}?",
            category=category,
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=_TS,
            yes_price=0.5,
            spread=0.0,  # zero spread so fill_price == yes_price → PnL = size * 1.0
            volume=1.0,
            liquidity=1.0,
        )
    )
    return market_id, snapshot_id, estimate_id


def _add_estimate(
    session: Session,
    market_id: str,
    snapshot_id: str,
    estimate_id: str,
    *,
    signal_source: SignalSource,
    p_model: float = 0.7,
) -> None:
    EstimateRepository(session).add(
        Estimate(
            id=estimate_id,
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=p_model,
            confidence=0.8,
            lower=0.1,
            upper=0.9,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=signal_source,
        )
    )


def _open_trade(
    session: Session,
    *,
    market_id: str,
    estimate_id: str | None,
    signal_source: SignalSource,
    size: float = 50.0,
) -> str:
    trade_id = _uid(f"trade-{market_id}-{signal_source.value}")
    PaperLedger(session).open(
        PaperTrade(
            id=trade_id,
            market_id=market_id,
            estimate_id=estimate_id,
            side=Side.YES,
            entry_price=0.5,
            size=size,
            status=TradeStatus.OPEN,
            opened_at=_TS,
            signal_source=signal_source,
        )
    )
    return trade_id


def _resolve(session: Session, market_id: str) -> None:
    session.add(
        models.Resolution(
            market_id=uuid.UUID(market_id),
            outcome=1,
            resolved_at=_RESOLVE_AT,
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
# /api/paper by_channel tests
# ---------------------------------------------------------------------------


def test_paper_by_channel_realized_pnl_per_source(client: TestClient, session: Session) -> None:
    """Seed resolved trades across radar/smartmoney/arb; assert per-channel realized_pnl."""
    # radar trade — resolved WIN
    mkt_r, snap_r, est_r = _seed_market(session, "radar")
    _add_estimate(session, mkt_r, snap_r, est_r, signal_source=SignalSource.RADAR)
    _open_trade(session, market_id=mkt_r, estimate_id=est_r, signal_source=SignalSource.RADAR)
    _resolve(session, mkt_r)
    PaperLedger(session).resolve(mkt_r, outcome=1, at=_RESOLVE_AT)

    # smartmoney trade — resolved WIN
    mkt_s, snap_s, est_s = _seed_market(session, "smartmoney")
    _add_estimate(session, mkt_s, snap_s, est_s, signal_source=SignalSource.SMARTMONEY)
    _open_trade(
        session,
        market_id=mkt_s,
        estimate_id=est_s,
        signal_source=SignalSource.SMARTMONEY,
        size=30.0,
    )
    _resolve(session, mkt_s)
    PaperLedger(session).resolve(mkt_s, outcome=1, at=_RESOLVE_AT)

    # arb trade — resolved WIN (no estimate_id)
    mkt_a, _, _ = _seed_market(session, "arb")
    _open_trade(
        session, market_id=mkt_a, estimate_id=None, signal_source=SignalSource.ARB, size=20.0
    )
    _resolve(session, mkt_a)
    PaperLedger(session).resolve(mkt_a, outcome=1, at=_RESOLVE_AT)

    body = client.get("/api/paper/positions").json()

    # existing fields must still be present
    assert "positions" in body
    assert "resolved" in body
    assert "capital" in body

    # new field
    assert "by_channel" in body, "by_channel field missing from /api/paper/positions response"
    by_channel = body["by_channel"]

    assert "radar" in by_channel, "radar channel missing"
    assert "smartmoney" in by_channel, "smartmoney channel missing"
    assert "arb" in by_channel, "arb channel missing"

    # resolved WIN at 0.5 entry, spread=0.0 → pnl = size * (1 - 0.5) / 0.5 = size * 1.0
    assert by_channel["radar"]["realized_pnl"] == pytest.approx(50.0)  # 50 * 1.0
    assert by_channel["smartmoney"]["realized_pnl"] == pytest.approx(30.0)  # 30 * 1.0
    assert by_channel["arb"]["realized_pnl"] == pytest.approx(20.0)  # 20 * 1.0

    assert by_channel["radar"]["n_resolved"] == 1
    assert by_channel["smartmoney"]["n_resolved"] == 1
    assert by_channel["arb"]["n_resolved"] == 1


def test_paper_by_channel_locked_in_open_and_counts(client: TestClient, session: Session) -> None:
    """Open trades count toward locked_in_open and n_open per channel."""
    mkt_r, snap_r, est_r = _seed_market(session, "r-open")
    _add_estimate(session, mkt_r, snap_r, est_r, signal_source=SignalSource.RADAR)
    _open_trade(
        session,
        market_id=mkt_r,
        estimate_id=est_r,
        signal_source=SignalSource.RADAR,
        size=75.0,
    )

    mkt_s, snap_s, est_s = _seed_market(session, "s-open")
    _add_estimate(session, mkt_s, snap_s, est_s, signal_source=SignalSource.SMARTMONEY)
    _open_trade(
        session,
        market_id=mkt_s,
        estimate_id=est_s,
        signal_source=SignalSource.SMARTMONEY,
        size=40.0,
    )

    body = client.get("/api/paper/positions").json()
    by_channel = body["by_channel"]

    assert by_channel["radar"]["locked_in_open"] == pytest.approx(75.0)
    assert by_channel["radar"]["n_open"] == 1
    assert by_channel["radar"]["n_resolved"] == 0

    assert by_channel["smartmoney"]["locked_in_open"] == pytest.approx(40.0)
    assert by_channel["smartmoney"]["n_open"] == 1


def test_paper_by_channel_empty_when_no_trades(client: TestClient) -> None:
    """With no trades the by_channel dict should be empty (not missing)."""
    body = client.get("/api/paper/positions").json()
    assert "by_channel" in body
    assert body["by_channel"] == {}


def test_paper_by_channel_null_source_maps_to_unknown(client: TestClient, session: Session) -> None:
    """A trade with signal_source=NULL (legacy/unknown) must appear under 'unknown', not dropped."""
    from pythia.db import models as db_models

    market_id = _uid("null-src-mkt")
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id="null-src",
            question="Null source trade?",
            category="politics",
            resolution_rule="r",
        )
    )
    # Insert a trade row with NULL signal_source directly via the ORM model
    trade_id = _uid("null-src-trade")
    session.add(
        db_models.PaperTrade(
            id=uuid.UUID(trade_id),
            market_id=uuid.UUID(market_id),
            estimate_id=None,
            side="YES",
            entry_price=0.5,
            size=10.0,
            status=TradeStatus.OPEN,
            opened_at=_TS,
            signal_source=None,  # NULL in DB
        )
    )
    session.commit()

    body = client.get("/api/paper/positions").json()
    by_channel = body["by_channel"]

    assert "unknown" in by_channel, "NULL-source trade must appear under 'unknown' channel"
    assert by_channel["unknown"]["n_open"] == 1
    assert by_channel["unknown"]["locked_in_open"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# /api/calibration by_channel tests
# ---------------------------------------------------------------------------


def _seed_resolved_estimate(
    session: Session,
    key: str,
    *,
    signal_source: SignalSource,
    p_model: float = 0.7,
    yes_price: float = 0.5,
    outcome: int = 1,
    category: str = "politics",
) -> None:
    """Seed market + snapshot + estimate + resolution for calibration tests."""
    market_id = _uid(f"cal-ch-{key}-mkt")
    snapshot_id = _uid(f"cal-ch-{key}-snap")
    estimate_id = _uid(f"cal-ch-{key}-est")
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"cal-ch-{key}",
            question=f"Cal question {key}?",
            category=category,
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=_TS,
            yes_price=yes_price,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=estimate_id,
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=p_model,
            confidence=0.8,
            lower=0.1,
            upper=0.9,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=signal_source,
        )
    )
    session.add(
        models.Resolution(
            market_id=uuid.UUID(market_id),
            outcome=outcome,
            resolved_at=_RESOLVE_AT,
        )
    )
    session.commit()


def test_calibration_by_channel_brier_and_n(client: TestClient, session: Session) -> None:
    """Seed resolved estimates across channels; assert per-channel brier_system/brier_market/n."""
    _seed_resolved_estimate(
        session, "r1", signal_source=SignalSource.RADAR, p_model=0.7, yes_price=0.5
    )
    _seed_resolved_estimate(
        session, "r2", signal_source=SignalSource.RADAR, p_model=0.8, yes_price=0.4
    )
    _seed_resolved_estimate(
        session, "s1", signal_source=SignalSource.SMARTMONEY, p_model=0.6, yes_price=0.5
    )

    body = client.get("/api/calibration").json()

    # existing fields must still be present
    assert "categories" in body
    assert "reliability" in body
    assert "ev" in body
    assert "cohorts" in body
    assert "gates" in body

    # new field
    assert "by_channel" in body, "by_channel field missing from /api/calibration response"
    by_channel = body["by_channel"]

    assert "radar" in by_channel, "radar channel missing from calibration by_channel"
    assert "smartmoney" in by_channel, "smartmoney channel missing from calibration by_channel"

    radar = by_channel["radar"]
    assert radar["n"] == 2
    # brier_system = mean((0.7-1)^2, (0.8-1)^2) = mean(0.09, 0.04) = 0.065
    assert radar["brier_system"] == pytest.approx(0.065, abs=1e-6)
    # brier_market = mean((0.5-1)^2, (0.4-1)^2) = mean(0.25, 0.36) = 0.305
    assert radar["brier_market"] == pytest.approx(0.305, abs=1e-6)
    assert "brier_system" in radar
    assert "brier_market" in radar
    # delta = brier_market - brier_system (server-computed, not client-derived)
    assert "delta" in radar
    assert radar["delta"] == pytest.approx(radar["brier_market"] - radar["brier_system"], abs=1e-9)

    sm = by_channel["smartmoney"]
    assert sm["n"] == 1
    # brier_system = (0.6-1)^2 = 0.16; brier_market = (0.5-1)^2 = 0.25
    assert sm["brier_system"] == pytest.approx(0.16, abs=1e-6)
    assert sm["brier_market"] == pytest.approx(0.25, abs=1e-6)
    assert sm["delta"] == pytest.approx(sm["brier_market"] - sm["brier_system"], abs=1e-9)


def test_calibration_by_channel_low_n_channel_still_returns_n(
    client: TestClient, session: Session
) -> None:
    """A single-resolution channel must still appear with n=1 (not omitted)."""
    _seed_resolved_estimate(
        session, "lone", signal_source=SignalSource.RADAR, p_model=0.65, yes_price=0.5
    )

    body = client.get("/api/calibration").json()
    by_channel = body["by_channel"]

    assert "radar" in by_channel
    assert by_channel["radar"]["n"] == 1


def test_calibration_by_channel_arb_excluded(client: TestClient, session: Session) -> None:
    """ARB signal has no estimate → must not appear in calibration by_channel."""
    # Seed a radar estimate so there is at least one channel
    _seed_resolved_estimate(session, "radar-only", signal_source=SignalSource.RADAR)
    # Seed an arb-tagged paper trade WITHOUT estimate — arb trades have no estimate_id,
    # so they can't be in the calibration estimates at all. But let's also check with UNKNOWN.
    _seed_resolved_estimate(session, "unknown-est", signal_source=SignalSource.UNKNOWN, p_model=0.6)

    body = client.get("/api/calibration").json()
    by_channel = body["by_channel"]

    # arb should not appear (no estimates tagged ARB were seeded)
    assert "arb" not in by_channel


def test_calibration_by_channel_unknown_channel_included(
    client: TestClient, session: Session
) -> None:
    """UNKNOWN channel estimates (backfill residual) should be included in by_channel."""
    _seed_resolved_estimate(
        session, "unk1", signal_source=SignalSource.UNKNOWN, p_model=0.6, yes_price=0.5
    )

    body = client.get("/api/calibration").json()
    by_channel = body["by_channel"]

    assert "unknown" in by_channel
    assert by_channel["unknown"]["n"] == 1
