"""recalibration_models persistence + versioning against real Postgres (§4.7, PYT-33)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from pythia.db import models
from pythia.db.repositories import RecalibrationRepository
from pythia.eval.recalibration import CalibrationMap
from pythia.eval.service import RecalibrationService

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
            confidence=0.5,
            lower=0.0,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
        )
    )
    session.commit()
    session.add(models.Resolution(market_id=_uid(name), outcome=outcome, resolved_at=resolved_at))
    session.commit()


def _seed_politics(session: Session) -> None:
    _resolved(session, "a1", "politics", 0.8, 0, resolved_at=_TS + timedelta(days=1))
    _resolved(session, "a2", "politics", 0.8, 1, resolved_at=_TS + timedelta(days=2))
    _resolved(session, "a3", "politics", 0.2, 1, resolved_at=_TS + timedelta(days=3))
    _resolved(session, "a4", "politics", 0.2, 0, resolved_at=_TS + timedelta(days=4))


def test_save_and_load_active_map(session: Session) -> None:
    repo = RecalibrationRepository(session)
    cmap = CalibrationMap(method="platt", params={"a": 1.2, "b": -0.3})
    repo.save("politics", cmap, n_fit=80)

    loaded = repo.active_for_category("politics")
    assert loaded is not None
    stored_map, n_fit = loaded
    assert stored_map == cmap
    assert n_fit == 80


def test_saving_a_new_map_deactivates_the_previous(session: Session) -> None:
    repo = RecalibrationRepository(session)
    repo.save("politics", CalibrationMap(method="platt", params={"a": 1.0, "b": 0.0}), n_fit=60)
    repo.save(
        "politics",
        CalibrationMap(method="isotonic", params={"x": [0.0, 1.0], "y": [0.1, 0.9]}),
        n_fit=1200,
    )

    loaded = repo.active_for_category("politics")
    assert loaded is not None
    stored_map, n_fit = loaded
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
    cmap, n_fit = loaded
    assert cmap.method == "platt"
    assert n_fit == 4


def test_fit_and_store_below_min_n_stores_identity(session: Session) -> None:
    _seed_politics(session)
    RecalibrationService(session).fit_and_store(_TS + timedelta(days=100), method="platt", min_n=50)
    loaded = RecalibrationRepository(session).active_for_category("politics")
    assert loaded is not None
    cmap, n_fit = loaded
    assert cmap.method == "identity"
    assert n_fit == 4


def test_evaluate_reports_raw_vs_recalibrated_per_category(session: Session) -> None:
    _seed_politics(session)
    reports = RecalibrationService(session).evaluate(
        _TS + timedelta(days=100), method="platt", min_n=4
    )
    assert "politics" in reports
    # holdout: 4 eligible → train on the earliest 2, evaluate on the latest 2.
    assert reports["politics"].n == 2
    assert reports["politics"].brier_recalibrated <= reports["politics"].brier_raw
