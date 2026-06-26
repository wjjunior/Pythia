"""Post-mortem loop on real Postgres (§5.5): trigger → persist → harness-gated adoption."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import (
    CandidateStatus,
    Estimate,
    Evidence,
    FailureClass,
    ImprovementAction,
    Market,
    MarketSnapshot,
)
from pythia.config import Settings
from pythia.db.repositories import (
    EstimateRepository,
    ImprovementCandidateRepository,
    MarketRepository,
    PostmortemRepository,
    SnapshotRepository,
)
from pythia.eval.harness.models import Ablation, ConfigReport
from pythia.services.postmortem import PostmortemService

pytestmark = pytest.mark.integration

AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _seed_estimate(
    session: Session,
    name: str,
    *,
    p_model: float,
    confidence: float,
    p_model_calibrated: float | None = None,
) -> Estimate:
    market_id = _uid(name)
    snapshot_id = _uid(f"{name}-snap")
    MarketRepository(session).upsert(
        Market(
            id=market_id, external_id=name, question="q", category="politics", resolution_rule="r"
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=AT,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return EstimateRepository(session).add(
        Estimate(
            id=_uid(f"{name}-est"),
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=p_model,
            p_model_calibrated=p_model_calibrated,
            confidence=confidence,
            lower=max(0.0, p_model - 0.1),
            upper=min(1.0, p_model + 0.1),
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )


def _fresh_evidence() -> list[Evidence]:
    return [
        Evidence(
            id=_uid("ev"),
            market_id="m",
            source_id="s",
            observed_at=AT - timedelta(days=1),
            content="fresh",
        )
    ]


def _config(n: int, bpd: float, brier_delta: float) -> ConfigReport:
    return ConfigReport(
        name="variant",
        brier_system=0.2,
        brier_market=0.25,
        brier_delta=brier_delta,
        brier_per_dollar=bpd,
        mean_latency_ms=0.0,
        error_rate=0.0,
        total_cost_usd=1.0,
        n=n,
        by_category=[],
    )


def _positive_ablation(n: int = 50) -> Ablation:
    return Ablation(
        base=_config(n, 0.0, 0.0),
        variant=_config(n, 0.5, 0.05),
        brier_per_dollar_delta=0.5,
        brier_delta_change=0.05,
    )


def test_surprise_writes_postmortem_and_proposes_candidate(session: Session) -> None:
    estimate = _seed_estimate(session, "surprise", p_model=0.95, confidence=0.9)
    service = PostmortemService(session, _settings())

    postmortem = service.review_resolution(
        estimate, _fresh_evidence(), category="politics", outcome=0, ambiguous=False, asof=AT
    )
    assert postmortem is not None
    assert postmortem.failure_class == FailureClass.MODEL_OVERCONFIDENCE
    assert len(PostmortemRepository(session).list_for_market(estimate.market_id)) == 1

    proposed = ImprovementCandidateRepository(session).list_by_status(CandidateStatus.PROPOSED)
    assert len(proposed) == 1
    assert proposed[0].kind == ImprovementAction.RECALIBRATE
    # Guardrail: nothing is auto-adopted on resolution.
    assert ImprovementCandidateRepository(session).list_by_status(CandidateStatus.ADOPTED) == []


def test_detect_category_drift_uses_the_configured_window(session: Session) -> None:
    service = PostmortemService(session, _settings())  # default postmortem_drift_window = 20
    assert service.detect_category_drift([0.1] * 20 + [-0.05] * 20)  # was ahead, now behind
    assert not service.detect_category_drift([0.1] * 40)  # steadily ahead of the market


def test_should_measure_the_committed_forecast_for_surprise_and_brier(session: Session) -> None:
    # PYT-112: raw 0.7 is a calibrated loss (not surprising), but the committed (recalibrated) 0.95
    # IS a confident miss. The surprise trigger and the stored Brier must measure the committed
    # forecast the system acted on — not the raw estimator output.
    estimate = _seed_estimate(
        session, "committed-surprise", p_model=0.7, confidence=0.9, p_model_calibrated=0.95
    )
    service = PostmortemService(session, _settings())

    postmortem = service.review_resolution(
        estimate, _fresh_evidence(), category="politics", outcome=0, ambiguous=False, asof=AT
    )

    assert postmortem is not None  # fires on the committed 0.95, not the raw 0.7
    assert postmortem.brier == pytest.approx((0.95 - 0) ** 2)  # committed Brier, not raw 0.49


def test_calibrated_loss_writes_nothing(session: Session) -> None:
    estimate = _seed_estimate(session, "calibrated", p_model=0.7, confidence=0.7)
    service = PostmortemService(session, _settings())
    result = service.review_resolution(
        estimate, _fresh_evidence(), category="politics", outcome=0, ambiguous=False, asof=AT
    )
    assert result is None
    assert PostmortemRepository(session).list_for_market(estimate.market_id) == []
    assert ImprovementCandidateRepository(session).list_by_status(CandidateStatus.PROPOSED) == []


def test_non_structural_candidate_adopts_only_after_harness_validation(session: Session) -> None:
    estimate = _seed_estimate(session, "adopt", p_model=0.95, confidence=0.9)
    service = PostmortemService(session, _settings())
    service.review_resolution(
        estimate, _fresh_evidence(), category="politics", outcome=0, ambiguous=False, asof=AT
    )
    candidate = ImprovementCandidateRepository(session).list_by_status(CandidateStatus.PROPOSED)[0]

    adopted = service.validate_candidate(candidate.id, _positive_ablation())
    assert adopted.status == CandidateStatus.ADOPTED
    assert adopted.brier_per_dollar == pytest.approx(0.5)
    assert adopted.n == 50


def test_structural_candidate_waits_for_human_gate(session: Session) -> None:
    estimate = _seed_estimate(session, "structural", p_model=0.95, confidence=0.9)
    service = PostmortemService(session, _settings())
    # Ambiguous resolution ⇒ GATE_CATEGORY, a structural action.
    service.review_resolution(
        estimate, _fresh_evidence(), category="politics", outcome=0, ambiguous=True, asof=AT
    )
    candidate = ImprovementCandidateRepository(session).list_by_status(CandidateStatus.PROPOSED)[0]
    assert candidate.kind == ImprovementAction.GATE_CATEGORY

    pending = service.validate_candidate(candidate.id, _positive_ablation(), human_approved=False)
    assert pending.status == CandidateStatus.VALIDATING

    approved = service.validate_candidate(candidate.id, _positive_ablation(), human_approved=True)
    assert approved.status == CandidateStatus.ADOPTED
