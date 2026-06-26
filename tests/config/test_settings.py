"""Config-boundary invariants (PYT-105): active deploy slots cannot outnumber the tracked roster."""

import pytest
from pydantic import ValidationError

from pythia.config import Settings

_DB = "postgresql+psycopg://x/y"


def test_defaults_keep_active_slots_within_the_tracked_roster() -> None:
    settings = Settings(_env_file=None, database_url=_DB)
    assert settings.active_traders_max <= settings.tracked_traders_max


def test_active_slots_exceeding_the_tracked_roster_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, active_traders_max=11, tracked_traders_max=10)


def test_negative_roster_caps_and_ttl_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, tracked_traders_max=-1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trader_bench_ttl_seconds=-1)


def test_extreme_price_share_cap_outside_the_unit_interval_is_rejected() -> None:
    # A cap > 1 silently disables the scalper guard; < 0 hard-caps every wallet (PYT-116).
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trader_extreme_price_share_cap=1.5)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=_DB, trader_extreme_price_share_cap=-0.1)
