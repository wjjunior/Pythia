"""Integration tests (PYT-90): new-entry detection (AC-S1) + the pipeline evaluator (AC-S2)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlmodel import Session, select

from pythia.agents import StubLLMPort
from pythia.common import Market, MarketSnapshot, TrackedTrader, TraderState
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
)
from pythia.db.smartmoney import TrackedTraderRepo, TraderEntryRepo
from pythia.ingestion.falcon import FalconTrade
from pythia.orchestration import Pipeline
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.smartmoney.candidate import PipelineCandidateEvaluator
from pythia.smartmoney.detector import detect_new_entries

pytestmark = pytest.mark.integration

_TS = datetime(2026, 1, 9, 15, 51, tzinfo=UTC)


def _trader(session: Session) -> TrackedTrader:
    return TrackedTraderRepo(session).add(
        TrackedTrader(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "w")),
            wallet="0xwhale",
            state=TraderState.ACTIVE,
            added_at=_TS,
            state_since=_TS,
        )
    )


def _trade(condition_id: str, side: str, ts: datetime) -> FalconTrade:
    return FalconTrade(
        trade_id=f"{condition_id}:{side}:{ts.isoformat()}",
        wallet="0xwhale",
        condition_id=condition_id,
        outcome="Up",
        side=side,
        price=0.3,
        size=100.0,
        slug="slug",
        transaction_hash="0xhash",
        ts=ts,
    )


def test_detection_is_idempotent_and_triggers_on_increases_only(session: Session) -> None:
    trader = _trader(session)
    repo = TraderEntryRepo(session)
    trades = [_trade("0xmktA", "BUY", _TS), _trade("0xmktB", "SELL", _TS)]

    first = detect_new_entries(trader, trades, repo)
    assert [e.external_market for e in first] == ["0xmktA"]  # the BUY triggers; the SELL never does

    assert detect_new_entries(trader, trades, repo) == []  # re-poll → no duplicate triggers (AC-S1)

    recorded = repo.list_for_trader(trader.id, asof=_TS + timedelta(days=1))
    assert {e.external_market for e in recorded} == {
        "0xmktA",
        "0xmktB",
    }  # the exit is still recorded


def test_increase_triggers_even_when_a_same_ts_exit_is_listed_first(session: Session) -> None:
    trader = _trader(session)
    repo = TraderEntryRepo(session)
    # A SELL and a BUY on the same market at the same ts — the (trader, market, ts) key keeps one;
    # the increase must win regardless of input order, so it still triggers.
    trades = [_trade("0xmkt", "SELL", _TS), _trade("0xmkt", "BUY", _TS)]

    triggers = detect_new_entries(trader, trades, repo)

    assert [e.external_market for e in triggers] == [
        "0xmkt"
    ]  # the increase wins, order-independent
    recorded = repo.list_for_trader(trader.id, asof=_TS + timedelta(days=1))
    assert len(recorded) == 1 and recorded[0].is_increase is True


def _pipeline(session: Session) -> Pipeline:
    from pythia.common import SignalSource

    return Pipeline(
        session=session,
        llm=StubLLMPort(),
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=Settings(_env_file=None, database_url="postgresql+psycopg://x/y"),
        routing={"estimation": "stub", "critic": "stub"},
        source=SignalSource.SMARTMONEY,
    )


def test_pipeline_evaluator_runs_the_pipeline_and_persists_a_verdict(session: Session) -> None:
    market = MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "fm")),
            external_id="0xfalconmkt",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "fm-snap")),
            market_id=market.id,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    evaluator = PipelineCandidateEvaluator(session, _pipeline(session))

    market_id = evaluator.evaluate("0xfalconmkt", _TS, ["trader-1"])

    assert market_id == market.id
    estimate = EstimateRepository(session).latest_for_market(market.id)
    assert estimate is not None  # the pipeline ran, point-in-time
    assert ScoreRepository(session).get_by_estimate(estimate.id) is not None  # verdict persisted


def test_pipeline_evaluator_is_idempotent_on_an_already_estimated_snapshot(
    session: Session,
) -> None:
    # PYT-171: re-evaluating a market already estimated at its snapshot (by the pipeline tick or a
    # prior candidate) must NOT re-run — the deterministic estimate id would collide on
    # estimates_pkey and crash the detection tick. The candidate stays evaluated, idempotently.
    market = MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "dupmkt")),
            external_id="0xdupmkt",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "dupmkt-snap")),
            market_id=market.id,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    evaluator = PipelineCandidateEvaluator(session, _pipeline(session))

    assert evaluator.evaluate("0xdupmkt", _TS, ["t1"]) == market.id  # first run estimates + scores
    assert evaluator.evaluate("0xdupmkt", _TS, ["t2"]) == market.id  # second run must not raise

    estimates = session.exec(
        select(models.Estimate).where(models.Estimate.market_id == uuid.UUID(market.id))
    ).all()
    assert len(estimates) == 1  # the re-run was skipped, not duplicated


def test_pipeline_evaluator_skips_an_untracked_market(session: Session) -> None:
    evaluator = PipelineCandidateEvaluator(session, _pipeline(session))
    assert evaluator.evaluate("0xnot-in-our-db", _TS, ["t"]) is None  # no fetcher → local-only


def _raw_market(condition_id: str) -> dict[str, Any]:
    return {
        "conditionId": condition_id,
        "question": "Will the whale's pick resolve YES?",
        "category": "sports",
        "description": "rule",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.4", "0.6"]),
        "lastTradePrice": "0.4",
        "spread": 0.02,
        "volumeNum": 1000.0,
        "liquidityNum": 1000.0,
        "negRisk": False,
        "closed": False,
    }


class _FakeFetcher:
    def __init__(self, by_condition: dict[str, dict[str, Any]]) -> None:
        self._by = by_condition

    def fetch_market_by_condition(
        self, condition_id: str, *, include_closed: bool = False
    ) -> dict[str, Any] | None:
        return self._by.get(condition_id)


def test_pipeline_evaluator_ingests_a_triggered_market_on_demand(session: Session) -> None:
    # A tracked wallet entered a market outside our crawl: the evaluator fetches + ingests it.
    fetcher = _FakeFetcher({"0xuncrawled": _raw_market("0xuncrawled")})
    evaluator = PipelineCandidateEvaluator(
        session, _pipeline(session), market_fetcher=fetcher, clock=lambda: _TS
    )

    market_id = evaluator.evaluate("0xuncrawled", _TS, ["trader-1"])

    assert market_id is not None
    ingested = MarketRepository(session).get_by_external_id("0xuncrawled")
    assert ingested is not None and ingested.id == market_id  # fetched + ingested, not dropped
    estimate = EstimateRepository(session).latest_for_market(market_id)
    assert estimate is not None  # the pipeline then ran on the freshly-ingested market
    assert ScoreRepository(session).get_by_estimate(estimate.id) is not None  # verdict persisted


def test_pipeline_evaluator_skips_when_the_triggered_market_is_missing(session: Session) -> None:
    fetcher = _FakeFetcher({})  # condition not found upstream (closed/delisted)
    evaluator = PipelineCandidateEvaluator(
        session, _pipeline(session), market_fetcher=fetcher, clock=lambda: _TS
    )
    assert evaluator.evaluate("0xgone", _TS, ["t"]) is None  # fetch miss → SKIPPED, as before


def test_pipeline_evaluator_skips_a_closed_triggered_market(session: Session) -> None:
    raw = _raw_market("0xclosed")
    raw["closed"] = True
    evaluator = PipelineCandidateEvaluator(
        session,
        _pipeline(session),
        market_fetcher=_FakeFetcher({"0xclosed": raw}),
        clock=lambda: _TS,
    )

    assert evaluator.evaluate("0xclosed", _TS, ["t"]) is None  # already resolved → not ingested
    assert MarketRepository(session).get_by_external_id("0xclosed") is None


def test_pipeline_evaluator_degrades_when_the_triggered_fetch_raises(session: Session) -> None:
    class _Boom:
        def fetch_market_by_condition(
            self, condition_id: str, *, include_closed: bool = False
        ) -> dict[str, Any] | None:
            raise httpx.ReadError("connection reset")  # an httpx error _request never maps

    evaluator = PipelineCandidateEvaluator(
        session, _pipeline(session), market_fetcher=_Boom(), clock=lambda: _TS
    )
    assert evaluator.evaluate("0xboom", _TS, ["t"]) is None  # best-effort: never aborts the tick
