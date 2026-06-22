"""Calibration service over seeded resolved markets (§2.1, §5.2): the headline metric."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from pythia.common import Side, TradeStatus
from pythia.config import Settings
from pythia.db import models
from pythia.eval.gate import GateReason
from pythia.eval.service import CalibrationService, gate_thresholds_from_settings

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


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
    session: Session, name: str, p_model: float, *, created_at: datetime | None = None
) -> None:
    row = models.Estimate(
        id=_uid(f"{name}-est-{p_model}-{created_at}"),
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

    report = CalibrationService(session).report()
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


def test_gate_status_blocks_a_category_with_insufficient_evidence(session: Session) -> None:
    _resolved_market(session, "p1", "politics", q=0.6, p_model=0.8, outcome=1)
    session.commit()

    thresholds = gate_thresholds_from_settings(Settings(_env_file=None, database_url="x"))
    decisions = CalibrationService(session).gate_status(thresholds)

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

    market = {c.category: c for c in CalibrationService(session).report().categories}["politics"]
    blended = {c.category: c for c in CalibrationService(session).blended_report().categories}[
        "politics"
    ]
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

    report = CalibrationService(session).report()
    assert report.categories[0].n == 1
    assert report.categories[0].brier_system == pytest.approx((0.8 - 1) ** 2)


def test_reliability_curve_is_produced(session: Session) -> None:
    _resolved_market(session, "r1", "politics", q=0.5, p_model=0.2, outcome=0)
    _resolved_market(session, "r2", "politics", q=0.5, p_model=0.85, outcome=1)
    session.commit()

    report = CalibrationService(session).report()
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

    report = CalibrationService(session).report()
    assert report.ev is not None
    assert report.ev.n == 1
    assert report.ev.mean_predicted == pytest.approx(0.7 - 0.52)
    assert report.ev.mean_realized == pytest.approx(1.0 - 0.52)
