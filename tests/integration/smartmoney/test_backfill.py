"""Integration test (PYT-94, AC-S8): open-position backfill end-to-end on real Postgres.

Sweeps a wallet's open book on tracking start. Positions are derived by netting the wallet's Falcon
trades (PYT-132 — there is no positions endpoint); each tracked, allow-listed position is evaluated
at asof = now (the current snapshot, never the trader's historical entry) and lands in the ledger
tagged ``open_backfill``. Both derived outcomes share one tracked market, so the sweep dedupes them
to a single candidate.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from pythia.agents import StubLLMPort
from pythia.common import (
    CandidateSignalStatus,
    CandidateSource,
    Market,
    MarketSnapshot,
    TrackedTrader,
    TraderState,
)
from pythia.config import Settings
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.db.smartmoney import TrackedTraderRepo
from pythia.ingestion.falcon import FalconClient, FixtureFalconTransport
from pythia.orchestration import Pipeline
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.smartmoney.backfill import BackfillSweeper

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "falcon"
_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
# The condition_id of both BUY trades in trader_trades.json — netted into two open outcomes.
_TRACKED_COND = "0x584654c869a3f1868f3ad1248e11ea988f3c4e332002839c6cac0e6db94485ee"


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _pipeline(session: Session) -> Pipeline:
    return Pipeline(
        session=session,
        llm=StubLLMPort(),
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=_settings(),
        routing={"estimation": "stub", "critic": "stub"},
    )


def test_backfill_sweep_persists_open_backfill_verdict_at_now(session: Session) -> None:
    trader = TrackedTraderRepo(session).add(
        TrackedTrader(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "bf-w")),
            wallet="0x11514fb5",
            state=TraderState.TRIAL,
            added_at=_NOW,
            state_since=_NOW,
        )
    )
    market = MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "bf-mkt")),
            external_id=_TRACKED_COND,
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "bf-snap")),
            market_id=market.id,
            ts=_NOW,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    sweeper = BackfillSweeper(
        session, FalconClient(FixtureFalconTransport(_FIXTURES)), _pipeline(session), _settings()
    )

    signals = sweeper.sweep(trader.id, trader.wallet, now=_NOW, evaluated_today=0)

    assert len(signals) == 1  # both derived outcomes share the one tracked market → deduped to one
    signal = signals[0]
    assert signal.source is CandidateSource.OPEN_BACKFILL
    assert signal.asof == _NOW  # current price, never the trader's historical entry (AC-S8)
    assert signal.market_id == market.id
    assert signal.status is CandidateSignalStatus.EVALUATED
    assert EstimateRepository(session).latest_for_market(market.id) is not None  # verdict persisted
