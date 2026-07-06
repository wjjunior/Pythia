"""Fee/slippage single Settings source (PYT-189/190): score and fill share the same cost numbers.

With `Settings(trade_fee=0.02, trade_slippage=0.01)` and a "politics" market (taker rate 0.04):
- `Score.edge_net` == raw_edge - spread/2 - (trade_fee + taker_fee(0.04, yes_price)) - slippage
- `PaperTrade.entry_price` == fill_price(yes, side, spread, fee=0.02, slippage=0.01)
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.agents import StubLLMPort
from pythia.common import (
    AuditEvent,
    AuditKind,
    Estimate,
    Market,
    MarketSnapshot,
    Score,
    Side,
    SignalSource,
    Source,
)
from pythia.config import Settings
from pythia.db.repositories import (
    AuditRepository,
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.orchestration import Pipeline, ReviewGate
from pythia.paper import PaperLedger, fill_price
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.scoring.fees import taker_fee

pytestmark = pytest.mark.integration

# Stable seeding keys
MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-fill-cost-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-fill-cost-snapshot"))
SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-fill-cost-source"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt189-fill-cost-estimate"))
ASOF = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

YES_PRICE = 0.55
SPREAD = 0.04
TRADE_FEE = 0.02
TRADE_SLIPPAGE = 0.01
ROUTING = {"estimation": "stub", "critic": "stub"}


def _settings_with_costs() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://x/y",
        trade_fee=TRADE_FEE,
        trade_slippage=TRADE_SLIPPAGE,
        # lower min_confidence so the stub estimate passes the risk gate
        min_confidence=0.0,
        min_source_reliability=0.0,
    )


def _seed(session: Session) -> tuple[Market, MarketSnapshot]:
    market = MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt189-fill",
            question="Will PYT-189 succeed?",
            category="politics",
            resolution_rule="manual",
        )
    )
    snapshot = SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=ASOF,
            yes_price=YES_PRICE,
            spread=SPREAD,
            volume=5000.0,
            liquidity=10000.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_ID, name="reuters-pyt189", kind="news", reliability=0.9)
    )
    EvidenceStore(session, StubEmbeddingPort()).add(
        market_id=MARKET_ID,
        source_id=SOURCE_ID,
        observed_at=ASOF - timedelta(days=1),
        content="PYT-189 is on track for completion.",
    )
    return market, snapshot


def _seed_risk_decision(session: Session, estimate_id: str) -> None:
    AuditRepository(session).append(
        AuditEvent(
            kind=AuditKind.RISK_DECISION,
            market_id=MARKET_ID,
            payload={
                "estimate_id": estimate_id,
                "approved_size": 500.0,
                "vetoed": False,
                "paper_only": True,
                "reasons": [],
            },
        )
    )


def test_score_edge_net_reflects_settings_fee_and_slippage(session: Session) -> None:
    """Pipeline._score applies trade_fee + per-category taker fee + slippage to build_score.

    The market is "politics" (taker rate 0.04) with YES_PRICE=0.55 and ASOF 2026-06-01 (after the
    schedule effective date), so taker_fee(0.04, 0.55) = 0.04 * 0.55 * 0.45 = 0.0099 is added on
    top of TRADE_FEE. The assertion is strictly tighter than before — it now verifies the full
    per-category fee, not just the flat rate.
    """
    settings = _settings_with_costs()
    market, snapshot = _seed(session)

    pipeline = Pipeline(
        session=session,
        llm=StubLLMPort(),
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=settings,
        routing=ROUTING,
        source=SignalSource.RADAR,
    )
    result = pipeline.run(market, snapshot)

    assert result.score is not None, "pipeline produced no score"
    score = result.score

    # raw_edge = p_model - yes_price; the stub sets p_model deterministically.
    # politics rate 0.04, snapshot ts 2026-06-01 ≥ schedule effective 2026-01-01.
    raw_edge = score.edge
    expected_fee = TRADE_FEE + taker_fee(0.04, YES_PRICE)
    expected_edge_net = raw_edge - SPREAD / 2 - expected_fee - TRADE_SLIPPAGE

    assert abs(score.edge_net - expected_edge_net) < 1e-9, (
        f"edge_net {score.edge_net!r} != raw_edge-spread/2-fee-slippage ({expected_edge_net!r})"
    )


def test_paper_trade_entry_price_reflects_settings_fee_and_slippage(session: Session) -> None:
    """ReviewGate fill uses fee_fn(category, p, opened_at) + slippage — no double-count.

    The market is "politics" (taker rate 0.04) with YES_PRICE=0.55 and ASOF 2026-06-01 (after the
    schedule effective date). The production PaperLedger is built with fee_fn, so the fill fee is
    TRADE_FEE + taker_fee(0.04, 0.55) = 0.02 + 0.04*0.55*0.45 = 0.02 + 0.0099 = 0.0299.
    flat self._fee is NOT added on top (the two never combine).
    """
    from pythia.scoring.fees import taker_fee_fn

    settings = _settings_with_costs()
    _seed(session)

    # Seed an estimate + score + approved risk decision so ReviewGate.approve can proceed
    estimate_id = ESTIMATE_ID
    EstimateRepository(session).add(
        Estimate(
            id=estimate_id,
            market_id=MARKET_ID,
            snapshot_id=SNAPSHOT_ID,
            p_model=0.65,
            confidence=0.8,
            lower=0.55,
            upper=0.75,
            rationale="stub",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.RADAR,
        )
    )
    ScoreRepository(session).add(
        Score(
            estimate_id=estimate_id,
            edge=0.1,
            edge_net=0.07,
            ev_unit=0.07,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.0336,
            kelly_fraction=0.155,
            signal_source=SignalSource.RADAR,
        )
    )
    _seed_risk_decision(session, estimate_id)

    # Mirror production: the router builds PaperLedger with fee_fn, not fee=.
    # fee_fn(category="politics", p=0.55, asof=ASOF) = TRADE_FEE + taker_fee(0.04, 0.55)
    ledger = PaperLedger(
        session,
        fee_fn=taker_fee_fn(
            settings.taker_fee_schedule, settings.taker_fee_default_rate, settings.trade_fee
        ),
        slippage=settings.trade_slippage,
    )
    gate = ReviewGate(session, ledger, settings)
    trade = gate.approve(estimate_id, side=Side.YES, size=100.0, at=ASOF, reviewer="test")

    # politics rate 0.04, snapshot ts 2026-06-01 >= effective 2026-01-01
    politics_taker = taker_fee(0.04, YES_PRICE)  # 0.04 * 0.55 * 0.45 = 0.0099
    total_fee = TRADE_FEE + politics_taker  # 0.02 + 0.0099 = 0.0299
    expected_entry = fill_price(YES_PRICE, Side.YES, SPREAD, fee=total_fee, slippage=TRADE_SLIPPAGE)
    assert abs(trade.entry_price - expected_entry) < 1e-9, (
        f"entry_price {trade.entry_price!r} != expected {expected_entry!r} "
        f"(fee={total_fee!r} = trade_fee {TRADE_FEE!r} + taker {politics_taker!r})"
    )
