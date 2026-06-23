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
        )
    )


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
    assert body["positions"] == []
    assert body["capital"]["realized_pnl"] > 0.0


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
