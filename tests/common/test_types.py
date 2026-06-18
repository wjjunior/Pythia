"""Validation tests for the §4.1 Pydantic contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pythia.common import (
    Estimate,
    Market,
    MarketSnapshot,
    Side,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _market() -> Market:
    return Market(
        id="m1",
        external_id="ext-1",
        question="Will it rain?",
        category="weather",
        resolution_rule="NWS official record",
    )


def test_market_defaults_for_optional_fields() -> None:
    market = _market()
    assert market.resolved is False
    assert market.resolution_source is None
    assert market.close_time is None


def test_side_serialises_to_yes_and_no() -> None:
    assert Side.YES.value == "YES"
    assert Side.NO.value == "NO"


def test_snapshot_rejects_price_outside_unit_interval() -> None:
    with pytest.raises(ValidationError):
        MarketSnapshot(
            id="s1",
            market_id="m1",
            ts=_TS,
            yes_price=1.5,
            spread=0.01,
            volume=1.0,
            liquidity=1.0,
        )


def test_estimate_rejects_confidence_outside_unit_interval() -> None:
    with pytest.raises(ValidationError):
        Estimate(
            id="e1",
            market_id="m1",
            snapshot_id="s1",
            p_model=0.5,
            confidence=2.0,
            lower=0.4,
            upper=0.6,
            rationale="because",
            evidence_ids=[],
            model_name="stub",
        )


def test_contracts_are_frozen() -> None:
    market = _market()
    with pytest.raises(ValidationError):
        market.resolved = True  # type: ignore[misc]
