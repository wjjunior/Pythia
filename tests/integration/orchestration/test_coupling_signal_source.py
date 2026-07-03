"""PYT-188 T5b: _couple_event inherits the parent estimate's signal_source (not a hardcoded value).

The invariant: a coupled child estimate must carry the same signal_source as its parent, so a
smartmoney-triggered estimate that gets coupled stays smartmoney, not radar or unknown.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from pythia.common import Estimate, Market, MarketSnapshot, SignalSource
from pythia.config import Settings
from pythia.db.repositories import EstimateRepository, MarketRepository, SnapshotRepository
from pythia.orchestration.runner import run_event_pass

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _seed_option(
    session: Session,
    event_slug: str,
    name: str,
    *,
    calibrated: float,
    signal_source: SignalSource,
) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5b-{event_slug}-{name}-market"))
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5b-{event_slug}-{name}-snap"))
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5b-{event_slug}-{name}-est"))

    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"0xpyt188-t5b-{event_slug}-{name}",
            question=f"Will {name} win {event_slug}?",
            category="politics",
            resolution_rule="r",
            event_slug=event_slug,
            neg_risk=True,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=snapshot_id,
            market_id=market_id,
            ts=_NOW,
            yes_price=0.5,
            spread=0.0,
            volume=1.0,
            liquidity=1.0,
        )
    )
    EstimateRepository(session).add(
        Estimate(
            id=estimate_id,
            market_id=market_id,
            snapshot_id=snapshot_id,
            p_model=calibrated,
            confidence=0.8,
            lower=0.0,
            upper=1.0,
            rationale="r",
            evidence_ids=[],
            model_name="stub",
            p_model_calibrated=calibrated,
            p_model_calibrated_fit_asof=_NOW,
            signal_source=signal_source,
        )
    )
    return market_id


def test_couple_event_inherits_smartmoney_source_from_parent(session: Session) -> None:
    # Three exclusive options whose calibrated probabilities sum > 1 → coupling fires.
    # All parents are SMARTMONEY; coupled children must also be SMARTMONEY, not RADAR/UNKNOWN.
    for name, calibrated in (("a", 0.5), ("b", 0.4), ("c", 0.3)):
        _seed_option(
            session,
            "pyt188-sm-cup",
            name,
            calibrated=calibrated,
            signal_source=SignalSource.SMARTMONEY,
        )

    run_event_pass(session, _settings(), now=_NOW)

    estimates = EstimateRepository(session)
    for name in ("a", "b", "c"):
        market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5b-pyt188-sm-cup-{name}-market"))
        latest = estimates.latest_for_market(market_id)
        assert latest is not None, f"no estimate for option {name}"
        assert latest.model_name == "event-coupled", f"option {name} was not coupled"
        assert latest.signal_source is SignalSource.SMARTMONEY, (
            f"option {name} coupled child has {latest.signal_source}, expected SMARTMONEY"
        )


def test_couple_event_inherits_radar_source_from_parent(session: Session) -> None:
    # Same structure but parents are RADAR — coupled children must inherit RADAR.
    for name, calibrated in (("a", 0.5), ("b", 0.4), ("c", 0.3)):
        _seed_option(
            session,
            "pyt188-radar-cup",
            name,
            calibrated=calibrated,
            signal_source=SignalSource.RADAR,
        )

    run_event_pass(session, _settings(), now=_NOW)

    estimates = EstimateRepository(session)
    for name in ("a", "b", "c"):
        market_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"pyt188-t5b-pyt188-radar-cup-{name}-market")
        )
        latest = estimates.latest_for_market(market_id)
        assert latest is not None, f"no estimate for option {name}"
        assert latest.model_name == "event-coupled", f"option {name} was not coupled"
        assert latest.signal_source is SignalSource.RADAR, (
            f"option {name} coupled child has {latest.signal_source}, expected RADAR"
        )
