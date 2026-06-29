"""Integration (PYT-141): the event pass detects + persists structural arbitrage."""

import uuid
from datetime import UTC, datetime
from itertools import pairwise

import pytest
from sqlmodel import Session, select

from pythia.common import Estimate, Market, MarketSnapshot
from pythia.config import Settings
from pythia.db import models
from pythia.db.repositories import (
    ArbRepository,
    EstimateRepository,
    MarketRepository,
    SnapshotRepository,
)
from pythia.orchestration.runner import run_event_pass

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://x/y")


def _seed_option(
    session: Session, event_slug: str, name: str, *, yes_price: float, resolved: bool = False
) -> str:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_slug}-{name}"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"0x{event_slug}-{name}",
            question=f"Will {name} win?",
            category="politics",
            resolution_rule="r",
            event_slug=event_slug,
            neg_risk=True,
            resolved=resolved,
        )
    )
    SnapshotRepository(session).append(
        MarketSnapshot(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-{market_id}")),
            market_id=market_id,
            ts=_NOW,
            yes_price=yes_price,
            spread=0.0,
            volume=1.0,
            liquidity=1.0,
        )
    )
    return market_id


def test_event_pass_detects_and_persists_a_dutch_book(session: Session) -> None:
    # Three mutually-exclusive options whose asks (spread 0) sum to 0.9 < 1 → a locked dutch book.
    for name in ("a", "b", "c"):
        _seed_option(session, "cup", name, yes_price=0.3)

    count = run_event_pass(session, _settings(), now=_NOW)

    assert count == 1
    arbs = ArbRepository(session).list_ranked()
    assert len(arbs) == 1
    assert arbs[0].ev == pytest.approx(0.1)  # 1 - 0.9
    assert len(arbs[0].legs) == 3


def test_event_pass_persists_nothing_for_a_consistently_priced_event(session: Session) -> None:
    _seed_option(session, "cup", "a", yes_price=0.6)
    _seed_option(session, "cup", "b", yes_price=0.5)  # asks sum 1.1 >= 1 → no arb
    assert run_event_pass(session, _settings(), now=_NOW) == 0
    assert ArbRepository(session).list_ranked() == []


def test_event_pass_does_not_flag_a_complete_negrisk_event(session: Session) -> None:
    # PYT-147 regression: any two of these legs (0.4 + 0.4) look like a dutch book, but the complete
    # three-leg set (asks sum 1.1 ≥ 1) is not arbitrable. A complete event must emit no arb.
    _seed_option(session, "match", "home", yes_price=0.4)
    _seed_option(session, "match", "away", yes_price=0.4)
    _seed_option(session, "match", "draw", yes_price=0.3)
    assert run_event_pass(session, _settings(), now=_NOW) == 0
    assert ArbRepository(session).list_ranked() == []


def test_event_pass_excludes_resolved_options(session: Session) -> None:
    # The two open options form a near-complete dutch book (asks 0.45 + 0.45 = 0.9 < 1, above the
    # coverage floor); a resolved option must not enter the group — pulling it in (yes 1.0) would
    # push the sum >= 1 and hide the real arb.
    _seed_option(session, "cup", "a", yes_price=0.45)
    _seed_option(session, "cup", "b", yes_price=0.45)
    _seed_option(session, "cup", "done", yes_price=1.0, resolved=True)
    assert run_event_pass(session, _settings(), now=_NOW) == 1
    assert len(ArbRepository(session).list_ranked()[0].legs) == 2  # only the open options


def test_event_pass_rejects_a_grossly_incomplete_dutch_book(session: Session) -> None:
    # PYT-170: two legs of a many-outcome event (e.g. 2 of ~30 exact scores) whose asks sum to 0.02
    # are not a complete exhaustive set — buying YES on both is not an arb. Below the coverage floor
    # it must NOT be flagged, even though 0.02 < 1.
    _seed_option(session, "exact-score", "0-2", yes_price=0.013)
    _seed_option(session, "exact-score", "3-3", yes_price=0.0075)
    assert run_event_pass(session, _settings(), now=_NOW) == 0
    assert ArbRepository(session).list_ranked() == []


def _seed_committed(
    session: Session,
    event_slug: str,
    name: str,
    *,
    question: str,
    calibrated: float,
    neg_risk: bool,
) -> tuple[str, str]:
    market_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_slug}-{name}"))
    MarketRepository(session).upsert(
        Market(
            id=market_id,
            external_id=f"0x{event_slug}-{name}",
            question=question,
            category="politics",
            resolution_rule="r",
            event_slug=event_slug,
            neg_risk=neg_risk,
        )
    )
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"snap-{market_id}"))
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
    estimate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"est-{market_id}"))
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
        )
    )
    return market_id, estimate_id


def _market_id(event_slug: str, name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event_slug}-{name}"))


def _committed(session: Session, market_id: str) -> tuple[str, float]:
    estimate = EstimateRepository(session).latest_for_market(market_id)
    assert estimate is not None and estimate.p_model_calibrated is not None
    return estimate.model_name, estimate.p_model_calibrated


def _estimate_count(session: Session) -> int:
    return len(session.exec(select(models.Estimate)).all())


def test_event_pass_couples_mutually_exclusive_committed_forecasts(session: Session) -> None:
    # Three exclusive options whose committed forecasts sum to 1.2 ⇒ not a structurally-valid set.
    ids = {
        name: _seed_committed(
            session, "cup", name, question=f"Will {name} win?", calibrated=c, neg_risk=True
        )
        for name, c in (("a", 0.5), ("b", 0.4), ("c", 0.3))
    }

    run_event_pass(session, _settings(), now=_NOW)

    results = {name: _committed(session, mid) for name, (mid, _) in ids.items()}
    assert all(model == "event-coupled" for model, _ in results.values())
    assert sum(value for _, value in results.values()) == pytest.approx(1.0)  # simplex restores Σ=1
    raw = EstimateRepository(session).get(ids["a"][1])  # append-only: the source is unmutated
    assert raw is not None and raw.model_name == "stub" and raw.p_model_calibrated == 0.5


def test_event_pass_couples_threshold_ladder_to_non_increasing(session: Session) -> None:
    # Nested "above $N billion" thresholds; committed values violate monotonicity (0.4 < 0.6).
    mids: dict[int, str] = {}
    for name, billions, calibrated in (("low", 1, 0.4), ("mid", 2, 0.6), ("high", 3, 0.2)):
        market_id, _ = _seed_committed(
            session,
            "rev",
            name,
            question=f"Will revenue exceed ${billions} billion?",
            calibrated=calibrated,
            neg_risk=False,
        )
        mids[billions] = market_id

    run_event_pass(session, _settings(), now=_NOW)

    by_threshold = [_committed(session, mids[b]) for b in sorted(mids)]  # ascending threshold
    # Violating legs get re-coupled; a leg already consistent keeps its raw estimate (append-only).
    assert any(model == "event-coupled" for model, _ in by_threshold)
    values = [value for _, value in by_threshold]
    assert all(a >= b for a, b in pairwise(values))  # isotonic non-increasing


def test_event_pass_coupling_is_idempotent(session: Session) -> None:
    for name, calibrated in (("a", 0.5), ("b", 0.4), ("c", 0.3)):
        _seed_committed(
            session, "cup", name, question=f"Will {name} win?", calibrated=calibrated, neg_risk=True
        )

    run_event_pass(session, _settings(), now=_NOW)
    after_first = _estimate_count(session)
    assert after_first == 6  # 3 raw + 3 coupled ⇒ coupling actually appended
    run_event_pass(
        session, _settings(), now=_NOW
    )  # the latest per option is already coupled ⇒ no re-append
    assert _estimate_count(session) == after_first
    assert _committed(session, _market_id("cup", "a"))[0] == "event-coupled"
