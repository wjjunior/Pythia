"""Config (Appendix A.3) boots in paper mode with zero secrets and fails fast."""

import pytest
from pydantic import ValidationError

from pythia.config import ExecutionModeError, Settings, ensure_paper_mode

_DB = "postgresql+psycopg://pythia:pythia@localhost:5434/pythia"


def test_boots_in_paper_mode_without_secrets() -> None:
    settings = Settings(_env_file=None, database_url=_DB)
    assert settings.mode == "paper"
    assert settings.openrouter_api_key is None
    assert settings.llm_enabled is False
    assert settings.use_live_venues is False
    ensure_paper_mode(settings)


def test_fails_fast_when_database_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_paper_mode_guard_rejects_live() -> None:
    settings = Settings(_env_file=None, database_url=_DB, mode="live")
    with pytest.raises(ExecutionModeError):
        ensure_paper_mode(settings)


def test_cross_venue_sources_are_parsed() -> None:
    settings = Settings(
        _env_file=None,
        database_url=_DB,
        cross_venue_sources="kalshi, metaculus ,manifold",
    )
    assert settings.cross_venue_source_list == ["kalshi", "metaculus", "manifold"]
