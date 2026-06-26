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


def test_falcon_agent_id_overrides_default_to_empty() -> None:
    assert Settings(_env_file=None, database_url=_DB).falcon_agent_id_overrides == {}


def test_falcon_agent_id_overrides_parse_a_dataset_id_map() -> None:
    # PYT-131: lets the operator correct a rotated Falcon agent_id from .env without a redeploy.
    settings = Settings(
        _env_file=None, database_url=_DB, falcon_agent_ids="trader_trades:556, market:574"
    )
    assert settings.falcon_agent_id_overrides == {"trader_trades": 556, "market": 574}


def test_falcon_agent_ids_missing_colon_is_rejected() -> None:
    settings = Settings(_env_file=None, database_url=_DB, falcon_agent_ids="trader_trades")
    with pytest.raises(ValueError, match="expected 'dataset:id'"):
        _ = settings.falcon_agent_id_overrides
