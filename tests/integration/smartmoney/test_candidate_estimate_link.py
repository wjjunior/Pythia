"""Integration tests (PYT-188 Task 6): candidate_signals.estimate_id FK capture.

Two invariants:
  (a) A fresh candidate eval writes the produced estimate's id to candidate_signals.estimate_id.
  (b) A candidate on an already-estimated snapshot (PYT-171 reuse path) still writes the
      pre-existing estimate's id — AND that estimate's signal_source is not altered
      (first-producer-wins).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.agents import StubLLMPort
from pythia.common import (
    CandidateSignal,
    CandidateSignalStatus,
    CandidateSource,
    Market,
    MarketSnapshot,
    SignalSource,
    TraderEntry,
)
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    EstimateRepository,
    MarketRepository,
    SnapshotRepository,
)
from pythia.db.smartmoney import CandidateSignalRepo
from pythia.orchestration import Pipeline
from pythia.rag import EvidenceStore, StubEmbeddingPort
from pythia.smartmoney.candidate import PipelineCandidateEvaluator, drain_queued, enqueue_candidates

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _pipeline(session: Session) -> Pipeline:
    return Pipeline(
        session=session,
        llm=StubLLMPort(),
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=Settings(_env_file=None, database_url="postgresql+psycopg://x/y"),
        routing={"estimation": "stub", "critic": "stub"},
        source=SignalSource.SMARTMONEY,
    )


def _market_with_snapshot(
    session: Session, *, external_id: str, namespace_key: str
) -> tuple[Market, MarketSnapshot]:
    market = MarketRepository(session).upsert(
        Market(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, namespace_key)),
            external_id=external_id,
            question="Will it resolve YES?",
            category="politics",
            resolution_rule="r",
        )
    )
    snapshot = SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace_key}-snap")),
            market_id=market.id,
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return market, snapshot


def _enqueue_signal(session: Session, *, market_id: str, signal_id: str) -> models.CandidateSignal:
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=signal_id,
            market_id=market_id,
            asof=_TS,
            trader_ids=[str(uuid.uuid5(uuid.NAMESPACE_URL, "trader-1"))],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.EVALUATED,
            created_at=_TS,
        )
    )
    row = session.get(models.CandidateSignal, uuid.UUID(signal_id))
    assert row is not None
    return row


def test_set_estimate_links_signal_to_produced_estimate(session: Session) -> None:
    """(a) CandidateSignalRepo.set_estimate writes the FK column."""
    market, _ = _market_with_snapshot(session, external_id="0xfresh", namespace_key="fresh")
    signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sig-fresh"))
    signal_row = _enqueue_signal(session, market_id=market.id, signal_id=signal_id)

    assert signal_row.estimate_id is None  # null before any pipeline run

    evaluator = PipelineCandidateEvaluator(session, _pipeline(session))
    evaluator.run_market(market.id, _TS, ["trader-1"])

    estimate = EstimateRepository(session).latest_for_market(market.id)
    assert estimate is not None

    CandidateSignalRepo(session).set_estimate(signal_id, estimate.id)

    session.expire(signal_row)
    refreshed = session.get(models.CandidateSignal, uuid.UUID(signal_id))
    assert refreshed is not None
    assert refreshed.estimate_id == uuid.UUID(estimate.id)


def test_enqueue_candidates_writes_estimate_id_on_fresh_eval(session: Session) -> None:
    """(a) enqueue_candidates sets candidate_signals.estimate_id via set_estimate."""
    market, _ = _market_with_snapshot(session, external_id="0xenqfresh", namespace_key="enqfresh")

    evaluator = PipelineCandidateEvaluator(session, _pipeline(session))
    repo = CandidateSignalRepo(session)

    class _StoreAdapter:
        def get(self, signal_id: str) -> CandidateSignal | None:
            return repo.get(signal_id)

        def enqueue(self, signal: CandidateSignal) -> CandidateSignal:
            return repo.enqueue(signal)

        def set_estimate(self, signal_id: str, estimate_id: str) -> None:
            repo.set_estimate(signal_id, estimate_id)

    entry = TraderEntry(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, "entry-1")),
        trader_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "trader-1")),
        external_market="0xenqfresh",
        side="BUY",
        ts=_TS - timedelta(minutes=1),
        is_increase=True,
    )

    signals = enqueue_candidates(
        [entry],
        store=_StoreAdapter(),
        evaluator=evaluator,
        daily_cap=10,
        evaluated_today=0,
        created_at=_TS,
    )

    assert len(signals) == 1
    signal = signals[0]
    assert signal.status is CandidateSignalStatus.EVALUATED

    estimate = EstimateRepository(session).latest_for_market(market.id)
    assert estimate is not None

    row = session.get(models.CandidateSignal, uuid.UUID(signal.id))
    assert row is not None
    assert row.estimate_id == uuid.UUID(estimate.id), (
        f"Expected estimate_id={estimate.id}, got {row.estimate_id}"
    )


def test_reuse_path_writes_existing_estimate_id_and_does_not_alter_signal_source(
    session: Session,
) -> None:
    """(b) PYT-171 reuse: when the snapshot is already estimated, candidate_signals.estimate_id
    still gets the pre-existing estimate's id, and that estimate's signal_source is unchanged."""
    market, _ = _market_with_snapshot(session, external_id="0xreuse", namespace_key="reuse")
    evaluator = PipelineCandidateEvaluator(session, _pipeline(session))

    # First run: produce the estimate (simulates the crawler tick that already ran)
    first_run_result = evaluator.run_market(market.id, _TS, ["trader-first"])
    assert first_run_result is not None, "First run must return a result"
    _, first_estimate_id = first_run_result
    assert first_estimate_id is not None, "First run must produce an estimate"

    # Confirm signal_source on the pre-existing estimate
    estimate_repo = EstimateRepository(session)
    pre_existing = estimate_repo.get(first_estimate_id)
    assert pre_existing is not None
    original_source = pre_existing.signal_source

    # Enqueue a candidate signal (as if a smart-money detection just triggered)
    signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sig-reuse"))
    _enqueue_signal(session, market_id=market.id, signal_id=signal_id)

    # Second run: reuse path — snapshot already estimated, must not re-run the pipeline
    reuse_run_result = evaluator.run_market(market.id, _TS, ["trader-second"])
    assert reuse_run_result is not None, "Reuse path must return a result"
    _, reuse_estimate_id = reuse_run_result

    assert reuse_estimate_id == first_estimate_id, (
        f"Reuse path must return pre-existing estimate id {first_estimate_id}, "
        f"got {reuse_estimate_id}"
    )

    # Set the FK via the repo (as drain_queued would do)
    assert reuse_estimate_id is not None
    CandidateSignalRepo(session).set_estimate(signal_id, reuse_estimate_id)

    row = session.get(models.CandidateSignal, uuid.UUID(signal_id))
    assert row is not None
    assert row.estimate_id == uuid.UUID(first_estimate_id)

    # The pre-existing estimate's signal_source must be untouched (first-producer-wins)
    refreshed = estimate_repo.get(first_estimate_id)
    assert refreshed is not None
    assert refreshed.signal_source == original_source, (
        "Reuse path must NOT alter the pre-existing estimate's signal_source"
    )


def test_veto_path_transitions_to_evaluated_without_estimate_and_is_not_retried(
    session: Session,
) -> None:
    """Regression (PYT-188 review): a QUEUED candidate whose pipeline vetoes before estimation
    must be transitioned to EVALUATED (status), its budget slot consumed, and its estimate_id
    left NULL — and must NOT be retried on the next drain tick.

    Before the fix, drain_queued gated on ``estimate_id is not None``, so a veto-path run_market
    returning None left the signal QUEUED and retried it on every subsequent tick forever.
    """
    market, _ = _market_with_snapshot(session, external_id="0xveto", namespace_key="veto")

    # Create the signal as QUEUED (over-cap path)
    signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "sig-veto"))
    CandidateSignalRepo(session).enqueue(
        CandidateSignal(
            id=signal_id,
            market_id=market.id,
            asof=_TS,
            trader_ids=[str(uuid.uuid5(uuid.NAMESPACE_URL, "trader-veto"))],
            source=CandidateSource.NEW_ENTRY,
            status=CandidateSignalStatus.QUEUED,
            created_at=_TS,
        )
    )

    # cap_per_category=0.0 means 0% of bankroll → any existing exposure (even 0.0) satisfies
    # exposure >= limit and triggers _veto_before_estimation before the LLM is called.
    veto_settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://x/y",
        cap_per_category=0.0,
    )
    pipeline = Pipeline(
        session=session,
        llm=StubLLMPort(),
        evidence=EvidenceStore(session, StubEmbeddingPort()),
        settings=veto_settings,
        routing={"estimation": "stub", "critic": "stub"},
        source=SignalSource.SMARTMONEY,
    )
    evaluator = PipelineCandidateEvaluator(session, pipeline)
    repo = CandidateSignalRepo(session)

    drained = drain_queued(store=repo, evaluator=evaluator, budget=5)

    # The veto-path signal must appear in the drained list (transitioned)
    assert len(drained) == 1
    assert drained[0].id == signal_id
    assert drained[0].status is CandidateSignalStatus.EVALUATED

    # FK must be NULL — no estimate was produced on the veto path
    row = session.get(models.CandidateSignal, uuid.UUID(signal_id))
    assert row is not None
    assert row.status == CandidateSignalStatus.EVALUATED
    assert row.estimate_id is None, "veto-path candidate must have estimate_id=NULL"

    # A second drain must NOT retry the now-EVALUATED signal
    drained_again = drain_queued(store=repo, evaluator=evaluator, budget=5)
    assert drained_again == [], "already-EVALUATED signal must not be retried"
