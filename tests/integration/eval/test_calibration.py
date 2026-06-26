"""Calibration service over seeded resolved markets (§2.1, §5.2): the headline metric."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import Side, TradeStatus
from pythia.config import Settings
from pythia.db import models
from pythia.eval.gate import GateReason
from pythia.services.calibration import CalibrationService, gate_thresholds_from_settings

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="x")


def _uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


def _market(session: Session, name: str, category: str) -> None:
    session.add(
        models.Market(
            id=_uid(name),
            external_id=name,
            question="q",
            category=category,
            resolution_rule="r",
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


def _estimate(
    session: Session,
    name: str,
    p_model: float,
    *,
    created_at: datetime | None = None,
    p_model_calibrated: float | None = None,
    p_model_calibrated_fit_asof: datetime | None = None,
) -> None:
    row = models.Estimate(
        id=_uid(f"{name}-est-{p_model}-{created_at}"),
        market_id=_uid(name),
        snapshot_id=_uid(f"{name}-snap"),
        p_model=p_model,
        p_model_calibrated=p_model_calibrated,
        p_model_calibrated_fit_asof=p_model_calibrated_fit_asof,
        confidence=0.5,
        lower=0.0,
        upper=1.0,
        rationale="r",
        evidence_ids=[],
        model_name="stub",
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    session.commit()


def _resolution(session: Session, name: str, outcome: int, *, ambiguous: bool = False) -> None:
    session.add(
        models.Resolution(
            market_id=_uid(name),
            outcome=outcome,
            resolved_at=_TS + timedelta(days=10),
            ambiguous=ambiguous,
        )
    )
    session.commit()


def _resolved_market(
    session: Session,
    name: str,
    category: str,
    q: float,
    p_model: float,
    outcome: int,
    *,
    ambiguous: bool = False,
) -> None:
    _market(session, name, category)
    _snapshot(session, name, q)
    _estimate(session, name, p_model)
    _resolution(session, name, outcome, ambiguous=ambiguous)


def test_per_category_brier_with_n_excluding_ambiguous(session: Session) -> None:
    _resolved_market(session, "p1", "politics", q=0.6, p_model=0.8, outcome=1)
    _resolved_market(session, "p2", "politics", q=0.5, p_model=0.3, outcome=0)
    _resolved_market(session, "s1", "sports", q=0.7, p_model=0.9, outcome=1)
    _resolved_market(session, "a1", "politics", q=0.5, p_model=0.5, outcome=0, ambiguous=True)
    session.commit()

    report = CalibrationService(session, settings=_settings()).report()
    by_cat = {c.category: c for c in report.categories}

    assert by_cat["politics"].n == 2
    assert by_cat["politics"].brier_system == pytest.approx((0.04 + 0.09) / 2)
    assert by_cat["politics"].brier_market == pytest.approx((0.16 + 0.25) / 2)
    assert by_cat["politics"].delta == pytest.approx(0.205 - 0.065)
    assert by_cat["sports"].n == 1
    assert by_cat["sports"].brier_system == pytest.approx(0.01)
    assert report.ev is None
    # invalid-rate is surfaced per category, separate from Brier (n excludes the ambiguous market).
    assert by_cat["politics"].invalid_count == 1
    assert by_cat["politics"].invalid_rate == pytest.approx(1 / 3)
    assert by_cat["sports"].invalid_count == 0


def test_verdict_measures_the_committed_recalibrated_forecast_not_raw(session: Session) -> None:
    # PYT-100: the estimator output (raw 0.6) and the committed/sized forecast (recalibrated 0.8)
    # differ; the headline Brier must measure the committed 0.8 — what the system actually ran.
    _market(session, "c1", "politics")
    _snapshot(session, "c1", 0.5)
    _estimate(session, "c1", 0.6, p_model_calibrated=0.8)
    _resolution(session, "c1", 1)

    report = CalibrationService(session, settings=_settings()).report()
    politics = next(c for c in report.categories if c.category == "politics")

    assert politics.brier_system == pytest.approx(0.04)  # brier(0.8, 1) — committed, NOT 0.16 (raw)
    overall = next(c for c in report.cohorts if c.cohort == "overall")
    assert overall.brier_system == pytest.approx(
        0.04
    )  # cohorts + reliability follow the committed p


def test_verdict_falls_back_to_raw_when_committed_is_null(session: Session) -> None:
    # A pre-PYT-100 estimate (no p_model_calibrated) is measured on raw p_model (back-compatible).
    _resolved_market(session, "n1", "politics", q=0.5, p_model=0.7, outcome=1)

    report = CalibrationService(session, settings=_settings()).report()
    politics = next(c for c in report.categories if c.category == "politics")

    assert politics.brier_system == pytest.approx(0.09)  # brier(0.7, 1), the raw fallback


def test_gate_status_blocks_a_category_with_insufficient_evidence(session: Session) -> None:
    _resolved_market(session, "p1", "politics", q=0.6, p_model=0.8, outcome=1)
    session.commit()

    thresholds = gate_thresholds_from_settings(Settings(_env_file=None, database_url="x"))
    decisions = CalibrationService(session, settings=_settings()).gate_status(thresholds)

    politics = next(decision for decision in decisions if decision.category == "politics")
    assert politics.cleared is False
    assert GateReason.INSUFFICIENT_N in politics.reasons  # n=1 << gate_min_n=50


def test_blended_report_uses_the_venue_baseline(session: Session) -> None:
    _resolved_market(session, "p1", "politics", q=0.5, p_model=0.9, outcome=1)
    session.add(
        models.VenueQuote(
            market_id=_uid("p1"),
            venue="manifold",
            external_ref="m1",
            ts=_TS,
            yes_price=0.8,
            match_confidence=0.9,
        )
    )
    session.commit()

    svc = CalibrationService(session, settings=_settings())
    market = {c.category: c for c in svc.report().categories}["politics"]
    blended = {c.category: c for c in svc.blended_report().categories}["politics"]
    # market baseline q=0.5; blended = (0.5 + 0.8) / 2 = 0.65, against outcome 1.
    assert market.brier_market == pytest.approx((0.5 - 1) ** 2)
    assert blended.brier_market == pytest.approx((0.65 - 1) ** 2)


def test_latest_estimate_per_market_wins(session: Session) -> None:
    _market(session, "m1", "politics")
    _snapshot(session, "m1", 0.5)
    _estimate(session, "m1", 0.2, created_at=_TS)
    _estimate(session, "m1", 0.8, created_at=_TS + timedelta(hours=1))
    _resolution(session, "m1", 1)
    session.commit()

    report = CalibrationService(session, settings=_settings()).report()
    assert report.categories[0].n == 1
    assert report.categories[0].brier_system == pytest.approx((0.8 - 1) ** 2)


def test_should_exclude_post_resolution_estimate_from_brier(session: Session) -> None:
    _market(session, "m1", "politics")
    resolved_at = _TS + timedelta(days=10)
    session.add_all(
        [
            models.MarketSnapshot(
                id=_uid("m1-snap-pre"),
                market_id=_uid("m1"),
                ts=_TS,
                yes_price=0.5,
                spread=0.02,
                volume=1.0,
                liquidity=1.0,
            ),
            models.MarketSnapshot(
                id=_uid("m1-snap-post"),
                market_id=_uid("m1"),
                ts=resolved_at + timedelta(days=10),
                yes_price=0.5,
                spread=0.02,
                volume=1.0,
                liquidity=1.0,
            ),
        ]
    )
    session.commit()
    session.add(
        models.Estimate(
            id=_uid("m1-est-pre"),
            market_id=_uid("m1"),
            snapshot_id=_uid("m1-snap-pre"),
            p_model=0.8,
            confidence=0.5,
            lower=0.0,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            created_at=_TS,
        )
    )
    session.add(
        models.Estimate(
            id=_uid("m1-est-post"),
            market_id=_uid("m1"),
            snapshot_id=_uid("m1-snap-post"),
            p_model=0.1,
            confidence=0.5,
            lower=0.0,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            created_at=resolved_at + timedelta(days=11),
        )
    )
    session.commit()
    _resolution(session, "m1", 1)

    report = CalibrationService(session, settings=_settings()).report()
    # The post-resolution estimate (p=0.1) is the latest by created_at but sits on a snapshot taken
    # after resolution; only the pre-resolution forecast (p=0.8) may enter the verdict.
    assert report.categories[0].n == 1
    assert report.categories[0].brier_system == pytest.approx((0.8 - 1) ** 2)


def test_should_exclude_post_snapshot_quote_from_blended_baseline(session: Session) -> None:
    _resolved_market(session, "p1", "politics", q=0.5, p_model=0.9, outcome=1)
    session.add(
        models.VenueQuote(
            market_id=_uid("p1"),
            venue="manifold",
            external_ref="late",
            ts=_TS + timedelta(hours=1),
            yes_price=0.8,
            match_confidence=0.9,
        )
    )
    session.commit()

    blended = {
        c.category: c
        for c in CalibrationService(session, settings=_settings()).blended_report().categories
    }["politics"]
    # The quote postdates the estimate's snapshot (ts=_TS) so it cannot enter the baseline; the
    # blended market price stays q_market=0.5, not (0.5 + 0.8) / 2.
    assert blended.brier_market == pytest.approx((0.5 - 1) ** 2)


def test_should_exclude_a_committed_forecast_whose_map_was_fit_after_the_snapshot(
    session: Session,
) -> None:
    # PYT-111 (AC2): a committed forecast whose recalibration map was fit AFTER the estimate's
    # snapshot is replay lookahead — the verdict drops it. A clean sibling market stays measured.
    _market(session, "ok", "politics")
    _snapshot(session, "ok", 0.5)  # snapshot ts = _TS
    _estimate(session, "ok", 0.8, p_model_calibrated=0.8, p_model_calibrated_fit_asof=_TS)
    _resolution(session, "ok", 1)

    _market(session, "leak", "politics")
    _snapshot(session, "leak", 0.5)
    _estimate(
        session,
        "leak",
        0.3,
        p_model_calibrated=0.9,
        p_model_calibrated_fit_asof=_TS + timedelta(days=1),  # fit after the snapshot → lookahead
    )
    _resolution(session, "leak", 1)

    politics = {
        c.category: c for c in CalibrationService(session, settings=_settings()).report().categories
    }["politics"]
    assert politics.n == 1  # only the clean market; the lookahead-committed forecast is excluded
    assert politics.brier_system == pytest.approx((0.8 - 1) ** 2)  # the clean committed forecast


def test_reliability_curve_is_produced(session: Session) -> None:
    _resolved_market(session, "r1", "politics", q=0.5, p_model=0.2, outcome=0)
    _resolved_market(session, "r2", "politics", q=0.5, p_model=0.85, outcome=1)
    session.commit()

    report = CalibrationService(session, settings=_settings()).report()
    assert report.reliability
    assert sum(b.count for b in report.reliability) == 2


def test_ev_predicted_vs_realized_over_resolved_trades(session: Session) -> None:
    _resolved_market(session, "t1", "politics", q=0.5, p_model=0.7, outcome=1)
    session.add(
        models.PaperTrade(
            id=_uid("t1-trade"),
            market_id=_uid("t1"),
            estimate_id=_uid("t1-est-0.7-None"),
            side=Side.YES,
            entry_price=0.52,
            size=100.0,
            status=TradeStatus.RESOLVED,
            opened_at=_TS,
            resolved_at=_TS + timedelta(days=10),
            pnl=92.3,
        )
    )
    session.commit()

    report = CalibrationService(session, settings=_settings()).report()
    assert report.ev is not None
    assert report.ev.n == 1
    assert report.ev.mean_predicted == pytest.approx(0.7 - 0.52)
    assert report.ev.mean_realized == pytest.approx(1.0 - 0.52)
