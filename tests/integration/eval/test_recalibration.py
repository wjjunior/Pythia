"""recalibration_models persistence + versioning against real Postgres (§4.7, PYT-33)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pythia.db import models
from pythia.db.repositories import RecalibrationRepository
from pythia.eval.recalibration import CalibrationMap
from pythia.services.calibration import RecalibrationService

pytestmark = pytest.mark.integration

_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _uid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


def _resolved(
    session: Session,
    name: str,
    category: str,
    p_model: float,
    outcome: int,
    *,
    resolved_at: datetime,
    ambiguous: bool = False,
    p_model_calibrated: float | None = None,
) -> None:
    session.add(
        models.Market(
            id=_uid(name), external_id=name, question="q", category=category, resolution_rule="r"
        )
    )
    session.commit()
    session.add(
        models.MarketSnapshot(
            id=_uid(f"{name}-snap"),
            market_id=_uid(name),
            ts=_TS,
            yes_price=0.5,
            spread=0.02,
            volume=1.0,
            liquidity=1.0,
        )
    )
    session.commit()
    session.add(
        models.Estimate(
            id=_uid(f"{name}-est"),
            market_id=_uid(name),
            snapshot_id=_uid(f"{name}-snap"),
            p_model=p_model,
            p_model_calibrated=p_model_calibrated,
            confidence=0.5,
            lower=0.0,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )
    session.commit()
    session.add(
        models.Resolution(
            market_id=_uid(name), outcome=outcome, resolved_at=resolved_at, ambiguous=ambiguous
        )
    )
    session.commit()


def _seed_politics(session: Session) -> None:
    _resolved(session, "a1", "politics", 0.8, 0, resolved_at=_TS + timedelta(days=1))
    _resolved(session, "a2", "politics", 0.8, 1, resolved_at=_TS + timedelta(days=2))
    _resolved(session, "a3", "politics", 0.2, 1, resolved_at=_TS + timedelta(days=3))
    _resolved(session, "a4", "politics", 0.2, 0, resolved_at=_TS + timedelta(days=4))


def test_save_and_load_active_map(session: Session) -> None:
    repo = RecalibrationRepository(session)
    cmap = CalibrationMap(method="platt", params={"a": 1.2, "b": -0.3})
    repo.save("politics", cmap, n_fit=80, fit_asof=_TS)

    loaded = repo.active_for_category("politics")
    assert loaded is not None
    stored_map, n_fit, fit_asof = loaded
    assert stored_map == cmap
    assert n_fit == 80
    assert fit_asof == _TS


def test_saving_a_new_map_deactivates_the_previous(session: Session) -> None:
    repo = RecalibrationRepository(session)
    repo.save(
        "politics",
        CalibrationMap(method="platt", params={"a": 1.0, "b": 0.0}),
        n_fit=60,
        fit_asof=_TS,
    )
    repo.save(
        "politics",
        CalibrationMap(method="isotonic", params={"x": [0.0, 1.0], "y": [0.1, 0.9]}),
        n_fit=1200,
        fit_asof=_TS + timedelta(days=1),
    )

    loaded = repo.active_for_category("politics")
    assert loaded is not None
    stored_map, n_fit, _fit_asof = loaded
    assert stored_map.method == "isotonic"
    assert n_fit == 1200


def test_unknown_category_has_no_active_map(session: Session) -> None:
    assert RecalibrationRepository(session).active_for_category("nope") is None


def test_db_rejects_two_active_maps_for_one_category(session: Session) -> None:
    session.add(
        models.RecalibrationModel(
            category="politics", method="identity", params={}, n_fit=1, active=True
        )
    )
    session.commit()
    session.add(
        models.RecalibrationModel(
            category="politics", method="identity", params={}, n_fit=2, active=True
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_fit_and_store_persists_a_point_in_time_map(session: Session) -> None:
    _seed_politics(session)
    RecalibrationService(session).fit_and_store(_TS + timedelta(days=100), method="platt", min_n=4)
    loaded = RecalibrationRepository(session).active_for_category("politics")
    assert loaded is not None
    cmap, n_fit, fit_asof = loaded
    assert cmap.method == "platt"
    assert n_fit == 4
    assert fit_asof == _TS + timedelta(days=100)  # the logical asof, not wall-clock


def test_fit_and_store_below_min_n_stores_identity(session: Session) -> None:
    _seed_politics(session)
    RecalibrationService(session).fit_and_store(_TS + timedelta(days=100), method="platt", min_n=50)
    loaded = RecalibrationRepository(session).active_for_category("politics")
    assert loaded is not None
    cmap, n_fit, _fit_asof = loaded
    assert cmap.method == "identity"
    assert n_fit == 4


def test_should_exclude_ambiguous_resolutions_from_training(session: Session) -> None:
    _seed_politics(session)  # 4 non-ambiguous resolutions, all before asof
    _resolved(
        session, "amb", "politics", 0.5, 0, resolved_at=_TS + timedelta(days=5), ambiguous=True
    )
    RecalibrationService(session).fit_and_store(_TS + timedelta(days=100), method="platt", min_n=4)
    loaded = RecalibrationRepository(session).active_for_category("politics")
    assert loaded is not None
    _cmap, n_fit, _fit_asof = loaded
    assert n_fit == 4  # the ambiguous resolution is excluded from the fit


def test_evaluate_reports_raw_vs_recalibrated_per_category(session: Session) -> None:
    _seed_politics(session)
    reports = RecalibrationService(session).evaluate(
        _TS + timedelta(days=100), method="platt", min_n=4
    )
    assert "politics" in reports
    # holdout: 4 eligible → train on the earliest 2, evaluate on the latest 2.
    assert reports["politics"].n == 2
    assert reports["politics"].brier_recalibrated <= reports["politics"].brier_raw


def test_live_report_measures_the_applied_map_not_a_holdout(session: Session) -> None:
    # The applied map turned a badly-calibrated raw 0.2 into a committed 0.8 on a YES outcome:
    # live_report grades the forecast the system actually committed to, reflecting the live map.
    _resolved(
        session,
        "lr1",
        "politics",
        0.2,
        1,
        resolved_at=_TS + timedelta(days=1),
        p_model_calibrated=0.8,
    )
    reports = RecalibrationService(session).live_report()
    assert set(reports) == {"politics"}
    report = reports["politics"]
    assert report.n == 1
    assert report.brier_raw == pytest.approx((0.2 - 1) ** 2)  # raw estimator: 0.64
    assert report.brier_recalibrated == pytest.approx((0.8 - 1) ** 2)  # committed (live map): 0.04


def test_live_report_falls_back_to_raw_where_no_map_was_applied(session: Session) -> None:
    # No map applied (p_model_calibrated NULL) ⇒ committed == raw ⇒ the two Briers coincide.
    _resolved(session, "lr2", "sports", 0.3, 0, resolved_at=_TS + timedelta(days=1))
    report = RecalibrationService(session).live_report()["sports"]
    assert report.n == 1
    assert report.brier_raw == report.brier_recalibrated == pytest.approx(0.3**2)
