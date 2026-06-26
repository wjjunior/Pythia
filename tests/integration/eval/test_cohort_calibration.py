"""Integration (PYT-95, AC-17): stratified calibration + the trade-filter separation guardrail."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import AuditEvent, AuditKind, Score, Side
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import AuditRepository, ScoreRepository
from pythia.eval.cohorts import Cohort
from pythia.eval.service import CalibrationService
from pythia.orchestration import OutOfCohortError, ReviewGate
from pythia.paper import PaperLedger

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
# horizon baseline: _TS is the snapshot; resolves_at = _TS + horizon_days
# fast_7d: horizon <= 7 days AND 0.10 <= price <= 0.85
# midband: 0.10 <= price <= 0.85 (any horizon)


def _uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"cohort-calib-{name}")


def _market(
    session: Session,
    name: str,
    *,
    category: str = "politics",
    resolves_at: datetime | None = None,
    close_time: datetime | None = None,
) -> None:
    session.add(
        models.Market(
            id=_uid(name),
            external_id=f"cohort-{name}",
            question="q",
            category=category,
            resolution_rule="r",
            resolves_at=resolves_at,
            close_time=close_time,
        )
    )
    session.commit()


def _snapshot(session: Session, name: str, q: float) -> None:
    session.add(
        models.MarketSnapshot(
            id=_uid(f"{name}-snap"),
            market_id=_uid(name),
            ts=_TS,
            yes_price=q,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    session.commit()


def _estimate(session: Session, name: str, p_model: float) -> None:
    session.add(
        models.Estimate(
            id=_uid(f"{name}-est"),
            market_id=_uid(name),
            snapshot_id=_uid(f"{name}-snap"),
            p_model=p_model,
            confidence=0.5,
            lower=0.0,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )
    session.commit()


def _score(session: Session, name: str) -> None:
    ScoreRepository(session).add(
        Score(
            estimate_id=str(_uid(f"{name}-est")),
            edge=0.1,
            edge_net=0.08,
            ev_unit=0.08,
            p_resol=0.6,
            p_liq=1.0,
            p_time=1.0,
            p_corr=1.0,
            opportunity_score=0.0384,
            kelly_fraction=0.2,
        )
    )


def _risk_decision(session: Session, name: str) -> None:
    AuditRepository(session).append(
        AuditEvent(
            kind=AuditKind.RISK_DECISION,
            market_id=str(_uid(name)),
            payload={
                "estimate_id": str(_uid(f"{name}-est")),
                "approved_size": 1000.0,
                "vetoed": False,
                "paper_only": True,
                "reasons": [],
            },
        )
    )


def _resolution(session: Session, name: str, outcome: int, *, ambiguous: bool = False) -> None:
    session.add(
        models.Resolution(
            market_id=_uid(name),
            outcome=outcome,
            resolved_at=_TS + timedelta(days=15),
            ambiguous=ambiguous,
        )
    )
    session.commit()


def _resolved_market(
    session: Session,
    name: str,
    q: float,
    p_model: float,
    outcome: int,
    *,
    category: str = "politics",
    resolves_at: datetime | None = None,
) -> None:
    _market(session, name, category=category, resolves_at=resolves_at)
    _snapshot(session, name, q)
    _estimate(session, name, p_model)
    _resolution(session, name, outcome)


def _seed_three_cohort_groups(session: Session) -> None:
    """Seed 7 resolved markets spanning cohort membership.

    fast_7d (horizon <= 7, price in [0.10, 0.85]):
      - f1: q=0.5, resolves in 5d  → fast_7d + midband
      - f2: q=0.4, resolves in 3d  → fast_7d + midband

    midband only (horizon > 7, price in [0.10, 0.85]):
      - m1: q=0.6, resolves in 30d → midband only
      - m2: q=0.3, resolves in 60d → midband only

    neither cohort (horizon > 7, price out of band):
      - n1: q=0.95, resolves in 30d → neither (but always in overall)
      - n2: q=0.95, resolves in 20d → neither
      - n3: q=0.02, resolves in 60d → neither
    """
    _resolved_market(
        session, "f1", q=0.5, p_model=0.6, outcome=1, resolves_at=_TS + timedelta(days=5)
    )
    _resolved_market(
        session, "f2", q=0.4, p_model=0.55, outcome=0, resolves_at=_TS + timedelta(days=3)
    )
    _resolved_market(
        session, "m1", q=0.6, p_model=0.7, outcome=1, resolves_at=_TS + timedelta(days=30)
    )
    _resolved_market(
        session, "m2", q=0.3, p_model=0.4, outcome=0, resolves_at=_TS + timedelta(days=60)
    )
    _resolved_market(
        session, "n1", q=0.95, p_model=0.9, outcome=1, resolves_at=_TS + timedelta(days=30)
    )
    _resolved_market(
        session, "n2", q=0.95, p_model=0.8, outcome=1, resolves_at=_TS + timedelta(days=20)
    )
    _resolved_market(
        session, "n3", q=0.02, p_model=0.05, outcome=0, resolves_at=_TS + timedelta(days=60)
    )


def test_report_is_stratified_by_cohort_and_overall(session: Session) -> None:
    """overall.n == total resolved; fast_7d.n == 2; midband.n == 4; brier values are positive."""
    _seed_three_cohort_groups(session)

    settings = Settings(_env_file=None, database_url="x")
    report = CalibrationService(session, settings=settings).report()
    by = {c.cohort: c for c in report.cohorts}

    assert Cohort.OVERALL.value in by
    # All 7 resolved markets (none ambiguous)
    assert by[Cohort.OVERALL.value].n == 7
    # Non-trivial: both system and market brier are positive for a mixed set of outcomes
    assert by[Cohort.OVERALL.value].brier_system > 0.0
    assert by[Cohort.OVERALL.value].brier_market > 0.0

    # fast_7d: f1 (5d, 0.5) + f2 (3d, 0.4)
    assert Cohort.FAST_7D.value in by
    assert by[Cohort.FAST_7D.value].n == 2

    # midband: f1 (0.5) + f2 (0.4) + m1 (0.6) + m2 (0.3)
    assert Cohort.MIDBAND.value in by
    assert by[Cohort.MIDBAND.value].n == 4


def test_out_of_cohort_market_still_in_overall_and_reliability(session: Session) -> None:
    """A 30-day, 0.95-price market is outside all cohorts but still in overall + reliability."""
    # Seed only the "neither" markets so it's unambiguous
    _resolved_market(
        session, "out1", q=0.95, p_model=0.9, outcome=1, resolves_at=_TS + timedelta(days=30)
    )
    _resolved_market(
        session, "in1", q=0.5, p_model=0.6, outcome=1, resolves_at=_TS + timedelta(days=5)
    )

    settings = Settings(_env_file=None, database_url="x")
    report = CalibrationService(session, settings=settings).report()
    by = {c.cohort: c for c in report.cohorts}

    # overall covers both markets including the out-of-cohort one
    assert by[Cohort.OVERALL.value].n == 2

    # fast_7d covers only in1 (5d, 0.5)
    assert by[Cohort.FAST_7D.value].n == 1

    # reliability bins are also over all 2 markets
    assert sum(b.count for b in report.reliability) == 2


def test_trade_filter_does_not_restrict_the_measured_number(session: Session) -> None:
    """AC-17 guardrail (non-circular): calibration reads estimates+resolutions, never paper_trades.

    Proof structure:
    1. Seed 7 resolved markets spanning fast_7d (2) and non-fast_7d (5), each approvable.
    2. Baseline: no paper trades yet — CalibrationService covers all 7 via estimates+resolutions.
    3. Enable the fast_7d filter and attempt to approve all 7:
       - fast_7d markets (f1, f2) succeed → 2 trades in paper_trades.
       - non-fast_7d markets raise OutOfCohortError → paper_trades remains at 2.
    4. Assert the trade table is strictly sparser than the market set (filter genuinely restricted).
    5. Assert CalibrationService.report() is identical to baseline — measurement is unchanged.
    """
    _seed_three_cohort_groups(session)

    # Make every market approvable (score + non-vetoed RISK_DECISION)
    for name in ("f1", "f2", "m1", "m2", "n1", "n2", "n3"):
        _score(session, name)
        _risk_decision(session, name)

    # --- Step 2: baseline with NO paper trades ---
    settings = Settings(_env_file=None, database_url="x")
    baseline = CalibrationService(session, settings=settings).report()
    baseline_by = {c.cohort: c for c in baseline.cohorts}
    assert baseline_by[Cohort.OVERALL.value].n == 7  # sanity: all 7 resolved markets counted

    # --- Step 3: enable the fast_7d filter; approve all 7 (only 2 will succeed) ---
    filter_settings = Settings(
        _env_file=None,
        database_url="x",
        cohort_trade_filter_enabled=True,
        cohort_trade_filter=Cohort.FAST_7D,
    )
    gate = ReviewGate(session, PaperLedger(session), filter_settings)

    out_of_cohort_errors = 0
    trades_opened = 0
    for name in ("f1", "f2", "m1", "m2", "n1", "n2", "n3"):
        try:
            gate.approve(
                str(_uid(f"{name}-est")),
                side=Side.YES,
                size=100.0,
                at=_TS + timedelta(hours=1),
                reviewer="test",
            )
            trades_opened += 1
        except OutOfCohortError:
            out_of_cohort_errors += 1

    # --- Step 4: the filter genuinely restricted the trade stream ---
    # Only f1 and f2 are fast_7d; the other 5 raised OutOfCohortError.
    assert trades_opened == 2
    assert out_of_cohort_errors == 5
    assert trades_opened < 7  # trade table is strictly sparser than the full market set

    # --- Step 5: calibration is UNCHANGED — it reads estimates+resolutions, not paper_trades ---
    after = CalibrationService(session, settings=filter_settings).report()
    after_by = {c.cohort: c for c in after.cohorts}

    assert set(baseline_by.keys()) == set(after_by.keys())
    for cohort_name in baseline_by:
        assert baseline_by[cohort_name].n == after_by[cohort_name].n, (
            f"cohort {cohort_name!r}: n changed from {baseline_by[cohort_name].n} "
            f"to {after_by[cohort_name].n} — calibration must not read paper_trades"
        )
        assert baseline_by[cohort_name].brier_system == pytest.approx(
            after_by[cohort_name].brier_system
        ), f"cohort {cohort_name!r}: brier_system changed"

    baseline_cats = {c.category: c for c in baseline.categories}
    after_cats = {c.category: c for c in after.categories}
    assert set(baseline_cats.keys()) == set(after_cats.keys())
    for cat in baseline_cats:
        assert baseline_cats[cat].n == after_cats[cat].n
        assert baseline_cats[cat].brier_system == pytest.approx(after_cats[cat].brier_system)


def test_cohort_order_is_deterministic_overall_last(session: Session) -> None:
    """Cohort list is alphabetically sorted with 'overall' pinned last."""
    _seed_three_cohort_groups(session)

    settings = Settings(_env_file=None, database_url="x")
    report = CalibrationService(session, settings=settings).report()
    cohort_names = [c.cohort for c in report.cohorts]

    # overall must be last
    assert cohort_names[-1] == Cohort.OVERALL.value
    # real cohorts must be alphabetically sorted
    real_cohorts = cohort_names[:-1]
    assert real_cohorts == sorted(real_cohorts)
