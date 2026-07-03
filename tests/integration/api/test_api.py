"""API integration tests (§A.1/A.2): every route against seeded data, SSE, OpenAPI, health."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from pythia.api.app import create_app
from pythia.api.deps import get_session, settings_dependency
from pythia.api.events import SseChannel
from pythia.common import (
    AuditEvent,
    AuditKind,
    CostEvent,
    Estimate,
    Market,
    MarketSnapshot,
    PaperTrade,
    Score,
    Side,
    SignalSource,
    Source,
    TradeStatus,
)
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    AuditRepository,
    CostRepository,
    EstimateRepository,
    MarketRepository,
    ScoreRepository,
    SnapshotRepository,
    SourceRepository,
)
from pythia.orchestration import ResolutionService
from pythia.paper import PaperLedger
from pythia.rag import EvidenceStore, StubEmbeddingPort

pytestmark = pytest.mark.integration

MARKET_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt26-market"))
SNAPSHOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt26-snapshot"))
ESTIMATE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt26-estimate"))
SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt26-source"))
EVIDENCE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt26-evidence"))
TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


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


def _seed_scored_market(session: Session, *, category: str = "politics") -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt26",
            question="Will it resolve YES?",
            category=category,
            resolution_rule="rule",
            event_slug="will-it-resolve-yes",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_ID, name="reuters", kind="news", reliability=0.9)
    )
    session.add(
        models.Evidence(
            id=uuid.UUID(EVIDENCE_ID),
            market_id=uuid.UUID(MARKET_ID),
            source_id=uuid.UUID(SOURCE_ID),
            observed_at=TS - timedelta(days=1),
            content="evidence content",
        )
    )
    session.commit()
    EstimateRepository(session).add(
        Estimate(
            id=ESTIMATE_ID,
            market_id=MARKET_ID,
            snapshot_id=SNAPSHOT_ID,
            p_model=0.7,
            confidence=0.8,
            lower=0.6,
            upper=0.8,
            rationale="r",
            evidence_ids=[EVIDENCE_ID],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )
    ScoreRepository(session).add(
        Score(
            estimate_id=ESTIMATE_ID,
            edge=0.2,
            edge_net=0.19,
            ev_unit=0.19,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.0912,
            kelly_fraction=0.38,
            signal_source=SignalSource.UNKNOWN,
        )
    )


def _seed_market_scored_twice(session: Session) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt72",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="rule",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    session.commit()
    older = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt72-estimate-old"))
    newer = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt72-estimate-new"))
    for estimate_id, p_model in ((older, 0.9), (newer, 0.3)):
        EstimateRepository(session).add(
            Estimate(
                id=estimate_id,
                market_id=MARKET_ID,
                snapshot_id=SNAPSHOT_ID,
                p_model=p_model,
                confidence=0.8,
                lower=0.2,
                upper=0.95,
                rationale="r",
                evidence_ids=[],
                model_name="stub",
                signal_source=SignalSource.UNKNOWN,
            )
        )
    # The older estimate carries the HIGHER opportunity_score, so ranking alone would surface it;
    # only latest-per-market dedup keeps the newer (lower-scored) row.
    session.add(
        models.Score(
            estimate_id=uuid.UUID(older),
            edge=0.4,
            edge_net=0.39,
            ev_unit=0.39,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.09,
            kelly_fraction=0.3,
            created_at=TS,
        )
    )
    session.add(
        models.Score(
            estimate_id=uuid.UUID(newer),
            edge=0.1,
            edge_net=0.09,
            ev_unit=0.09,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.02,
            kelly_fraction=0.1,
            created_at=TS + timedelta(hours=1),
        )
    )
    session.commit()


def _seed_calibration_market(
    session: Session,
    *,
    key: str,
    close_time: datetime,
    yes_price: float,
    p_model: float,
    opp: float,
    resolves_at: datetime | None = None,
    resolved: bool = False,
    liquidity: float = 1.0,
) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{key}-snap"))
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{key}-est"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=key,
            question="q",
            category="politics",
            resolution_rule="r",
            close_time=close_time,
            resolves_at=resolves_at,
            resolved=resolved,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=TS,
            yes_price=yes_price,
            spread=0.0,
            volume=1.0,
            liquidity=liquidity,
        )
    )
    session.commit()
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
            signal_source=SignalSource.UNKNOWN,
        )
    )
    session.add(
        models.Score(
            estimate_id=uuid.UUID(estimate_id),
            edge=p_model - yes_price,
            edge_net=p_model - yes_price,
            ev_unit=p_model - yes_price,
            p_resol=1.0,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=opp,
            kelly_fraction=0.0,
            created_at=TS,
        )
    )
    session.commit()
    return market_id


def test_radar_calibration_mode_filters_by_window_and_win_multiple(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    a = _seed_calibration_market(
        session,
        key="cal-a",
        close_time=now + timedelta(days=5),
        yes_price=0.4,
        p_model=0.6,
        opp=0.09,
    )
    b = _seed_calibration_market(
        session,
        key="cal-b",
        close_time=now + timedelta(days=2),
        yes_price=0.5,
        p_model=0.7,
        opp=0.02,
    )
    _seed_calibration_market(  # in window but YES @ 0.9 → payout 1.11x, fails 1.4
        session,
        key="cal-c",
        close_time=now + timedelta(days=1),
        yes_price=0.9,
        p_model=0.95,
        opp=0.06,
    )
    _seed_calibration_market(  # good payout but 30d out, fails the window
        session,
        key="cal-d",
        close_time=now + timedelta(days=30),
        yes_price=0.4,
        p_model=0.6,
        opp=0.5,
    )
    _seed_calibration_market(  # already closed in the past — must not slip in ahead of near-term
        session,
        key="cal-past",
        close_time=now - timedelta(days=5),
        yes_price=0.4,
        p_model=0.6,
        opp=0.5,
    )

    body = client.get("/api/radar?max_days_to_resolve=7&min_win_multiple=1.4").json()

    assert [r["market"]["id"] for r in body] == [b, a]  # soonest first; c, d, past all filtered out


def test_radar_calibration_mode_filters_by_win_probability(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    good = _seed_calibration_market(  # recommended YES, win prob 0.7, pays 2x
        session,
        key="cal-prob-good",
        close_time=now + timedelta(days=2),
        yes_price=0.5,
        p_model=0.7,
        opp=0.05,
    )
    _seed_calibration_market(  # Belarus-type: YES pays 2000x but the model gives it 1% -> dropped
        session,
        key="cal-prob-longshot",
        close_time=now + timedelta(days=1),
        yes_price=0.0005,
        p_model=0.01,
        opp=0.5,
    )

    body = client.get(
        "/api/radar?max_days_to_resolve=7&min_win_multiple=1.4&min_win_probability=0.5"
    ).json()

    assert [r["market"]["id"] for r in body] == [good]  # the longshot is excluded despite 2000x
    assert body[0]["win_probability"] == pytest.approx(0.7)


def test_radar_applies_win_filters_without_a_close_window(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    good = _seed_calibration_market(
        session,
        key="nw-good",
        close_time=now + timedelta(days=2),
        yes_price=0.5,
        p_model=0.7,
        opp=0.05,
    )
    _seed_calibration_market(  # higher opp would rank first, but win prob 0.01 is filtered out
        session,
        key="nw-longshot",
        close_time=now + timedelta(days=1),
        yes_price=0.0005,
        p_model=0.01,
        opp=0.9,
    )

    body = client.get("/api/radar?min_win_probability=0.5").json()  # no max_days_to_close

    assert [r["market"]["id"] for r in body] == [good]


def test_radar_excludes_a_market_that_resolves_far_beyond_the_window(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    near = _seed_calibration_market(
        session,
        key="res-near",
        close_time=now + timedelta(days=5),
        resolves_at=now + timedelta(days=5),
        yes_price=0.4,
        p_model=0.6,
        opp=0.05,
    )
    _seed_calibration_market(  # OpenAI IPO case: trades close in 5d but resolution is ~6 months out
        session,
        key="res-far",
        close_time=now + timedelta(days=5),
        resolves_at=now + timedelta(days=191),
        yes_price=0.4,
        p_model=0.6,
        opp=0.5,
    )

    body = client.get("/api/radar?max_days_to_resolve=7").json()

    assert [r["market"]["id"] for r in body] == [near]  # far-resolution market excluded


def test_radar_excludes_a_market_whose_trading_has_already_closed(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    enterable = _seed_calibration_market(
        session,
        key="enter-open",
        close_time=now + timedelta(days=3),
        resolves_at=now + timedelta(days=3),
        yes_price=0.4,
        p_model=0.6,
        opp=0.05,
    )
    _seed_calibration_market(  # resolves in 2d but trading closed yesterday — no way to enter
        session,
        key="enter-closed",
        close_time=now - timedelta(days=1),
        resolves_at=now + timedelta(days=2),
        yes_price=0.4,
        p_model=0.6,
        opp=0.5,
    )

    body = client.get("/api/radar?max_days_to_resolve=7").json()

    assert [r["market"]["id"] for r in body] == [enterable]  # closed-trading market excluded


def test_radar_excludes_an_untraded_zero_liquidity_market(
    client: TestClient, session: Session
) -> None:
    # PYT-169: an untraded negRisk leg sits at a default 0.5 with zero liquidity — not enterable, so
    # it must not be surfaced as an opportunity even when its metrics look attractive.
    now = datetime.now(UTC)
    tradeable = _seed_calibration_market(
        session,
        key="liq-real",
        close_time=now + timedelta(days=3),
        resolves_at=now + timedelta(days=3),
        yes_price=0.4,
        p_model=0.6,
        opp=0.05,
        liquidity=5000.0,
    )
    _seed_calibration_market(  # placeholder leg: default 0.5 price, zero liquidity, can't be filled
        session,
        key="liq-placeholder",
        close_time=now + timedelta(days=1),
        resolves_at=now + timedelta(days=1),
        yes_price=0.5,
        p_model=0.02,
        opp=-0.3,
        liquidity=0.0,
    )

    body = client.get("/api/radar?max_days_to_resolve=7").json()

    assert [r["market"]["id"] for r in body] == [tradeable]  # zero-liquidity placeholder excluded


def test_radar_orders_by_soonest_resolution_not_trading_close(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    later = _seed_calibration_market(  # closes first (1d) but resolves later (6d)
        session,
        key="ord-later",
        close_time=now + timedelta(days=1),
        resolves_at=now + timedelta(days=6),
        yes_price=0.4,
        p_model=0.6,
        opp=0.05,
    )
    sooner = _seed_calibration_market(  # closes later (4d) but resolves first (2d)
        session,
        key="ord-sooner",
        close_time=now + timedelta(days=4),
        resolves_at=now + timedelta(days=2),
        yes_price=0.4,
        p_model=0.6,
        opp=0.05,
    )

    body = client.get("/api/radar?max_days_to_resolve=7").json()

    assert [r["market"]["id"] for r in body] == [sooner, later]  # soonest resolution first


def test_radar_excludes_a_confident_override_of_a_near_certain_market(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    keep = _seed_calibration_market(
        session,
        key="dis-keep",
        close_time=now + timedelta(days=5),
        yes_price=0.5,
        p_model=0.6,
        opp=0.05,
    )
    override = _seed_calibration_market(  # YES at 85% vs a market priced at 0.05% — excluded
        session,
        key="dis-override",
        close_time=now + timedelta(days=5),
        yes_price=0.0005,
        p_model=0.85,
        opp=0.5,
    )

    ids = [r["market"]["id"] for r in client.get("/api/radar?limit=10").json()]

    assert keep in ids
    assert override not in ids  # de-ranking was not enough; the row is dropped outright


def test_radar_keeps_a_longshot_the_model_also_rates_low(
    client: TestClient, session: Session
) -> None:
    now = datetime.now(UTC)
    longshot = _seed_calibration_market(  # model agrees the 0.05% market is a longshot — kept
        session,
        key="dis-agree",
        close_time=now + timedelta(days=5),
        yes_price=0.0005,
        p_model=0.02,
        opp=0.01,
    )

    ids = [r["market"]["id"] for r in client.get("/api/radar?limit=10").json()]

    assert longshot in ids


def test_radar_item_exposes_recommended_side_and_win_multiple(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)  # yes_price 0.5, p_model 0.7
    item = client.get("/api/radar?limit=10").json()[0]
    assert item["recommended_side"] == "YES"
    assert item["entry_price"] == pytest.approx(0.5)
    assert item["win_multiple"] == pytest.approx(2.0)
    assert item["win_probability"] == pytest.approx(0.7)
    assert "closes_in_days" in item
    assert "resolves_in_days" in item
    assert item["market"]["event_slug"] == "will-it-resolve-yes"  # drives the Polymarket deep-link


def _seed_risk_decision(session: Session, *, vetoed: bool, approved_size: float) -> None:
    AuditRepository(session).append(
        AuditEvent(
            kind=AuditKind.RISK_DECISION,
            market_id=MARKET_ID,
            payload={
                "estimate_id": ESTIMATE_ID,
                "approved_size": approved_size,
                "vetoed": vetoed,
                "paper_only": True,
                "reasons": [],
            },
        )
    )


def test_radar_item_exposes_the_risk_approved_suggested_size(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=False, approved_size=12.5)
    item = client.get("/api/radar?limit=10").json()[0]
    assert item["suggested_size"] == pytest.approx(12.5)  # PYT-172: drives the 1-click approve size


def test_radar_item_suggested_size_is_zero_when_vetoed(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=True, approved_size=0.0)
    item = client.get("/api/radar?limit=10").json()[0]
    assert item["suggested_size"] == 0.0  # vetoed ⇒ not approvable from the radar


def test_radar_item_suggested_size_is_zero_without_a_risk_decision(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)  # scored but never risk-evaluated
    item = client.get("/api/radar?limit=10").json()[0]
    assert item["suggested_size"] == 0.0


def test_list_markets_filters_by_category(client: TestClient, session: Session) -> None:
    _seed_scored_market(session, category="politics")
    assert len(client.get("/api/markets").json()) == 1
    assert len(client.get("/api/markets?category=politics").json()) == 1
    assert client.get("/api/markets?category=sports").json() == []


def test_list_markets_filters_by_status(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    resolved_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt44-resolved-market"))
    MarketRepository(session).upsert(
        Market(
            id=resolved_id,
            external_id="pyt44-resolved",
            question="q",
            category="politics",
            resolution_rule="r",
            resolved=True,
        )
    )
    session.commit()

    open_ids = {m["id"] for m in client.get("/api/markets?status=open").json()}
    resolved_ids = {m["id"] for m in client.get("/api/markets?status=resolved").json()}
    assert open_ids == {MARKET_ID}
    assert resolved_ids == {resolved_id}
    assert client.get("/api/markets?status=bogus").status_code == 422


def test_market_detail_includes_snapshot_estimate_and_evidence(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    body = client.get(f"/api/markets/{MARKET_ID}").json()
    assert body["market"]["id"] == MARKET_ID
    assert len(body["snapshots"]) == 1
    assert body["latest_estimate"]["id"] == ESTIMATE_ID
    assert body["evidence"][0]["id"] == EVIDENCE_ID


def test_market_detail_unknown_is_404(client: TestClient) -> None:
    missing = str(uuid.uuid5(uuid.NAMESPACE_URL, "missing"))
    assert client.get(f"/api/markets/{missing}").status_code == 404


def test_radar_ranks_opportunities(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    body = client.get("/api/radar?limit=10").json()
    assert len(body) == 1
    assert body[0]["score"]["opportunity_score"] == pytest.approx(0.0912)
    assert body[0]["market"]["id"] == MARKET_ID


def test_radar_shows_only_the_latest_score_per_market(client: TestClient, session: Session) -> None:
    _seed_market_scored_twice(session)
    rows = [r for r in client.get("/api/radar?limit=10").json() if r["market"]["id"] == MARKET_ID]
    assert len(rows) == 1
    assert rows[0]["score"]["opportunity_score"] == pytest.approx(0.02)
    assert rows[0]["estimate"]["p_model"] == pytest.approx(0.3)


def test_radar_preserves_ranking_across_markets_after_dedup(
    client: TestClient, session: Session
) -> None:
    _seed_market_scored_twice(session)
    other_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt72-other"))
    other_snapshot = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt72-other-snapshot"))
    other_estimate = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt72-other-estimate"))
    MarketRepository(session).upsert(
        Market(
            id=other_id,
            external_id="pyt72-other",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=other_snapshot,
            market_id=other_id,
            ts=TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    session.commit()
    EstimateRepository(session).add(
        Estimate(
            id=other_estimate,
            market_id=other_id,
            snapshot_id=other_snapshot,
            p_model=0.7,
            confidence=0.8,
            lower=0.6,
            upper=0.8,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )
    ScoreRepository(session).add(
        Score(
            estimate_id=other_estimate,
            edge=0.2,
            edge_net=0.19,
            ev_unit=0.19,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.05,
            kelly_fraction=0.2,
            signal_source=SignalSource.UNKNOWN,
        )
    )
    ids = [r["market"]["id"] for r in client.get("/api/radar?limit=10").json()]
    assert ids == [other_id, MARKET_ID]


def test_estimate_detail_includes_cited_evidence(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    body = client.get(f"/api/estimates/{ESTIMATE_ID}").json()
    assert body["estimate"]["id"] == ESTIMATE_ID
    assert body["evidence"][0]["id"] == EVIDENCE_ID


def test_paper_positions_reports_capital_state(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=False, approved_size=1000.0)
    client.post(
        f"/api/review/{ESTIMATE_ID}",
        json={"action": "approve", "side": "YES", "size": 100.0, "reviewer": "alice"},
    )
    body = client.get("/api/paper/positions").json()
    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    assert pos["trade"]["side"] == "YES"  # PYT-181: the trade is nested under an enriched view
    assert pos["question"] == "Will it resolve YES?"  # market metadata for the page
    assert pos["event_slug"] == "will-it-resolve-yes"  # drives the Polymarket deep-link
    assert body["resolved"] == []
    assert body["capital"]["locked_in_open"] == pytest.approx(100.0)
    assert body["capital"]["available_capital"] == pytest.approx(10_000.0 - 100.0)


def test_portfolio_reports_capital_and_exposure(client: TestClient, session: Session) -> None:
    _seed_scored_market(session, category="politics")
    _seed_risk_decision(session, vetoed=False, approved_size=1000.0)
    client.post(
        f"/api/review/{ESTIMATE_ID}", json={"action": "approve", "side": "YES", "size": 100.0}
    )
    body = client.get("/api/portfolio").json()
    assert body["capital"]["locked_in_open"] == pytest.approx(100.0)
    assert body["capital"]["available_capital"] == pytest.approx(10_000.0 - 100.0)
    assert body["exposure"]["per_category"]["politics"] == pytest.approx(100.0)
    assert body["exposure"]["absolute_total"] == pytest.approx(100.0)


def test_calibration_reports_categories(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    session.add(
        models.Resolution(
            market_id=uuid.UUID(MARKET_ID), outcome=1, resolved_at=TS + timedelta(days=10)
        )
    )
    session.commit()
    body = client.get("/api/calibration").json()
    assert body["categories"][0]["category"] == "politics"
    assert body["categories"][0]["n"] == 1
    # PYT-101: the §8.5 gate is surfaced per category; n=1 is below gate_min_n so it is not cleared.
    gate = next(g for g in body["gates"] if g["category"] == "politics")
    assert gate["cleared"] is False
    assert "insufficient_n" in gate["reasons"]


def test_recalibration_reports_live_raw_vs_recalibrated(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)  # estimate p_model=0.7, politics
    estimate = session.get(models.Estimate, uuid.UUID(ESTIMATE_ID))
    assert estimate is not None
    estimate.p_model_calibrated = 0.9  # the applied map's committed forecast
    session.add(estimate)
    session.add(
        models.Resolution(
            market_id=uuid.UUID(MARKET_ID), outcome=1, resolved_at=TS + timedelta(days=10)
        )
    )
    session.commit()
    body = client.get("/api/calibration/recalibration").json()
    assert body["politics"]["n"] == 1
    assert body["politics"]["brier_raw"] == pytest.approx((0.7 - 1) ** 2)  # raw estimator: 0.09
    assert body["politics"]["brier_recalibrated"] == pytest.approx(
        (0.9 - 1) ** 2
    )  # committed: 0.01


def _resolve_market(session: Session, *, outcome: int = 1) -> None:
    session.add(
        models.Resolution(
            market_id=uuid.UUID(MARKET_ID), outcome=outcome, resolved_at=TS + timedelta(days=10)
        )
    )
    session.commit()


def test_simulation_projects_from_measured_data(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    _resolve_market(session)
    body = client.get("/api/simulation").json()
    assert body["n"] == 1
    assert body["terminal_p5"] <= body["terminal_p50"] <= body["terminal_p95"]
    assert 0.0 <= body["probability_of_ruin"] <= 1.0
    assert len(body["growth_curve"]) > 0


def test_simulation_is_reproducible(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    _resolve_market(session)
    first = client.get("/api/simulation").json()
    second = client.get("/api/simulation").json()
    assert first == second


def test_simulation_without_measurements_is_404(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    _resolve_market(session)
    assert client.get("/api/simulation?category=sports").status_code == 404


def test_cost_reports_spend_breakdown(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    repo = CostRepository(session)
    repo.record(
        CostEvent(
            agent="estimation",
            task="estimate",
            provider="openrouter",
            model="m/x",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.04,
        ),
        market_id=MARKET_ID,
    )
    repo.record(
        CostEvent(
            agent="critic",
            task="critique",
            provider="openrouter",
            model="m/y",
            input_tokens=8,
            output_tokens=4,
            cost_usd=0.06,
        )
    )
    body = client.get("/api/cost").json()
    assert body["total"] == pytest.approx(0.10)
    assert body["by_agent"]["estimation"] == pytest.approx(0.04)
    assert body["by_model"]["m/y"] == pytest.approx(0.06)
    assert body["by_category"]["politics"] == pytest.approx(0.04)
    assert body["cost_per_opportunity"] == pytest.approx(0.10)
    assert len(body["by_day"]) == 1


def test_review_approve_opens_a_paper_trade(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=False, approved_size=1000.0)
    response = client.post(
        f"/api/review/{ESTIMATE_ID}", json={"action": "approve", "side": "YES", "size": 100.0}
    )
    assert response.status_code == 200
    assert response.json()["action"] == "approve"
    assert response.json()["trade"]["status"] == "open"


def test_review_approve_vetoed_opportunity_returns_409(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=True, approved_size=0.0)
    response = client.post(
        f"/api/review/{ESTIMATE_ID}", json={"action": "approve", "side": "YES", "size": 100.0}
    )
    assert response.status_code == 409


def test_review_reject_opens_nothing(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    response = client.post(f"/api/review/{ESTIMATE_ID}", json={"action": "reject"})
    assert response.status_code == 200
    assert response.json()["trade"] is None


def test_review_rejects_non_positive_size(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    response = client.post(
        f"/api/review/{ESTIMATE_ID}", json={"action": "approve", "side": "YES", "size": 0.0}
    )
    assert response.status_code == 422


def test_paper_positions_excludes_resolved_trades(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=False, approved_size=1000.0)
    client.post(
        f"/api/review/{ESTIMATE_ID}", json={"action": "approve", "side": "YES", "size": 100.0}
    )
    PaperLedger(session).resolve(MARKET_ID, outcome=1, at=TS + timedelta(days=5))
    body = client.get("/api/paper/positions").json()
    assert body["positions"] == []  # not among OPEN positions
    assert len(body["resolved"]) == 1  # PYT-181: shown in the resolved history instead
    assert body["resolved"][0]["trade"]["status"] == "resolved"
    assert body["capital"]["realized_pnl"] > 0.0


def test_close_position_voids_an_open_trade_and_releases_capital(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=False, approved_size=1000.0)
    client.post(
        f"/api/review/{ESTIMATE_ID}", json={"action": "approve", "side": "YES", "size": 100.0}
    )
    trade_id = client.get("/api/paper/positions").json()["positions"][0]["trade"]["id"]

    closed = client.post(f"/api/paper/positions/{trade_id}/close")
    assert closed.status_code == 200
    assert closed.json()["status"] == "void" and closed.json()["pnl"] == 0.0

    after = client.get("/api/paper/positions").json()
    assert after["positions"] == []  # no longer open
    assert after["capital"]["locked_in_open"] == pytest.approx(0.0)  # capital released
    assert after["capital"]["realized_pnl"] == pytest.approx(0.0)  # void ⇒ no realized PnL


def test_close_position_rejects_a_non_open_trade(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    _seed_risk_decision(session, vetoed=False, approved_size=1000.0)
    client.post(
        f"/api/review/{ESTIMATE_ID}", json={"action": "approve", "side": "YES", "size": 100.0}
    )
    trade_id = client.get("/api/paper/positions").json()["positions"][0]["trade"]["id"]
    assert client.post(f"/api/paper/positions/{trade_id}/close").status_code == 200
    assert client.post(f"/api/paper/positions/{trade_id}/close").status_code == 409  # already void


def test_close_position_unknown_is_404(client: TestClient) -> None:
    assert client.post(f"/api/paper/positions/{uuid.uuid4()}/close").status_code == 404


def test_radar_rejects_invalid_limit(client: TestClient) -> None:
    assert client.get("/api/radar?limit=-1").status_code == 422
    assert client.get("/api/radar?limit=0").status_code == 422


def test_non_uuid_path_is_422_not_500(client: TestClient) -> None:
    assert client.get("/api/markets/not-a-uuid").status_code == 422
    assert client.get("/api/estimates/not-a-uuid").status_code == 422


def test_review_unknown_estimate_is_404(client: TestClient) -> None:
    missing = str(uuid.uuid5(uuid.NAMESPACE_URL, "missing-estimate"))
    response = client.post(f"/api/review/{missing}", json={"action": "reject"})
    assert response.status_code == 404


def test_health_reports_db_and_freshness(client: TestClient, session: Session) -> None:
    _seed_scored_market(session)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["last_snapshot_at"] is not None


def test_sse_emits_a_published_event(app: FastAPI, client: TestClient) -> None:
    app.state.broadcaster.publish(SseChannel.RADAR_CHANGED, "market-xyz")
    response = client.get("/api/events?limit=1")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: radar_changed" in response.text
    assert "market-xyz" in response.text


def test_sse_flushes_an_initial_comment_so_the_connection_opens(client: TestClient) -> None:
    response = client.get("/api/events?limit=0")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert response.text.startswith(":")


def test_run_endpoint_scores_open_markets_and_emits_radar_changed(
    client: TestClient, session: Session
) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt41-run",
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=SNAPSHOT_ID,
            market_id=MARKET_ID,
            ts=TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    SourceRepository(session).upsert(
        Source(id=SOURCE_ID, name="reuters", kind="news", reliability=0.9)
    )
    EvidenceStore(session, StubEmbeddingPort()).add(
        market_id=MARKET_ID,
        source_id=SOURCE_ID,
        observed_at=TS - timedelta(days=1),
        content="Polls show the YES side leading.",
    )

    response = client.post("/api/run")
    assert response.status_code == 200
    body = response.json()
    assert body["scored"] >= 1
    assert body["halted"] is False

    events = client.get("/api/events?limit=1")
    assert "event: radar_changed" in events.text
    assert MARKET_ID in events.text


def test_resolution_emits_calibration_changed_over_sse(
    app: FastAPI, client: TestClient, session: Session
) -> None:
    MarketRepository(session).upsert(
        Market(
            id=MARKET_ID,
            external_id="pyt41-cal",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    broadcaster = app.state.broadcaster
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://x/y")
    ResolutionService(
        session,
        settings,
        PaperLedger(session),
        on_calibration_changed=lambda category: broadcaster.publish(
            SseChannel.CALIBRATION_CHANGED, category
        ),
    ).record(MARKET_ID, outcome=1, at=TS + timedelta(days=5))

    events = client.get("/api/events?limit=1")
    assert "event: calibration_changed" in events.text
    assert "politics" in events.text


def _open_trade_for(session: Session, *, market_id: str, estimate_id: str) -> None:
    PaperLedger(session).open(
        PaperTrade(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{estimate_id}-trade")),
            market_id=market_id,
            estimate_id=estimate_id,
            side=Side.YES,
            entry_price=0.5,
            size=100.0,
            status=TradeStatus.OPEN,
            opened_at=TS,
            signal_source=SignalSource.UNKNOWN,
        )
    )


def test_resolve_market_should_record_audit_and_settle(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    _open_trade_for(session, market_id=MARKET_ID, estimate_id=ESTIMATE_ID)

    response = client.post(f"/api/markets/{MARKET_ID}/resolve", json={"outcome": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["market_id"] == MARKET_ID
    assert body["outcome"] == 1
    assert body["ambiguous"] is False

    market = MarketRepository(session).get(MARKET_ID)
    assert market is not None and market.resolved is True
    kinds = {event.kind for event in AuditRepository(session).list_for_market(MARKET_ID)}
    assert AuditKind.RESOLUTION in kinds
    trade = session.get(models.PaperTrade, uuid.uuid5(uuid.NAMESPACE_URL, f"{ESTIMATE_ID}-trade"))
    assert trade is not None and trade.status == TradeStatus.RESOLVED and trade.pnl is not None


def test_resolve_market_should_publish_calibration_changed_over_sse(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)

    response = client.post(f"/api/markets/{MARKET_ID}/resolve", json={"outcome": 1})
    assert response.status_code == 200

    events = client.get("/api/events?limit=1")
    assert "event: calibration_changed" in events.text
    assert "politics" in events.text


def test_resolve_market_should_flag_postmortem_created_on_a_surprise(
    client: TestClient, session: Session
) -> None:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt45-surprise"))
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt45-surprise-snap"))
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt45-surprise-est"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id="pyt45-surprise",
            question="q",
            category="politics",
            resolution_rule="r",
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=TS,
            yes_price=0.5,
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
            p_model=0.95,
            confidence=0.9,
            lower=0.85,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            signal_source=SignalSource.UNKNOWN,
        )
    )

    response = client.post(f"/api/markets/{market_id}/resolve", json={"outcome": 0})

    assert response.status_code == 200
    assert response.json()["postmortem_created"] is True


def test_resolve_unknown_market_should_return_404(client: TestClient) -> None:
    missing = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt45-missing"))
    response = client.post(f"/api/markets/{missing}/resolve", json={"outcome": 1})
    assert response.status_code == 404


def test_resolve_already_resolved_market_should_return_409(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    first = client.post(f"/api/markets/{MARKET_ID}/resolve", json={"outcome": 1})
    assert first.status_code == 200

    second = client.post(f"/api/markets/{MARKET_ID}/resolve", json={"outcome": 1})
    assert second.status_code == 409


def test_resolve_should_reject_invalid_outcome_with_422(
    client: TestClient, session: Session
) -> None:
    _seed_scored_market(session)
    response = client.post(f"/api/markets/{MARKET_ID}/resolve", json={"outcome": 2})
    assert response.status_code == 422


def _seed_subjective_market(session: Session) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt60-subjective"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id="pyt60-subjective",
            question="Did the senate pass the bill?",
            category="politics",
            resolution_rule="UMA optimistic oracle",
            resolution_source="subjective_uma",
        )
    )
    return market_id


def test_propose_resolution_should_return_an_ambiguous_proposal_under_the_stub(
    client: TestClient, session: Session
) -> None:
    market_id = _seed_subjective_market(session)

    response = client.post(f"/api/markets/{market_id}/resolution/propose")

    assert response.status_code == 200
    body = response.json()
    assert body["market_id"] == market_id
    assert body["ambiguous"] is True
    assert body["outcome"] is None
    assert body["model_name"] == "stub"


def test_propose_resolution_unknown_market_should_return_404(client: TestClient) -> None:
    missing = str(uuid.uuid5(uuid.NAMESPACE_URL, "pyt60-missing"))
    response = client.post(f"/api/markets/{missing}/resolution/propose")
    assert response.status_code == 404


def test_propose_resolution_already_resolved_market_should_return_409(
    client: TestClient, session: Session
) -> None:
    market_id = _seed_subjective_market(session)
    assert client.post(f"/api/markets/{market_id}/resolve", json={"outcome": 1}).status_code == 200

    response = client.post(f"/api/markets/{market_id}/resolution/propose")
    assert response.status_code == 409


def test_openapi_schema_is_emitted(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/api/radar" in spec["paths"]
    assert "/api/review/{estimate_id}" in spec["paths"]
    assert "CalibrationResponse" in spec["components"]["schemas"]
