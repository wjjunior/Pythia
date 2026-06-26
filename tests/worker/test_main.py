"""The worker live-gate (§8.6, AC-S7): USE_LIVE_VENUES must guard all live construction."""

import pytest

from pythia.config import Settings
from pythia.worker import __main__ as worker_main

_DB = "postgresql+psycopg://x/y"


def _settings(*, use_live: bool) -> Settings:
    return Settings(_env_file=None, database_url=_DB, use_live_venues=use_live)


class _FakeScheduler:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True


def test_should_not_construct_the_live_worker_when_use_live_venues_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_main, "get_settings", lambda: _settings(use_live=False))
    monkeypatch.setattr(worker_main, "_configure", lambda settings: None)
    built: list[str] = []

    def _record(*_args: object, **_kwargs: object) -> _FakeScheduler:
        built.append("scheduler")
        return _FakeScheduler()

    monkeypatch.setattr(worker_main, "build_scheduler", _record)

    worker_main.main()

    assert built == []  # the gate returns before any live transport / scheduler is constructed


def test_should_start_the_worker_when_use_live_venues_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_main, "get_settings", lambda: _settings(use_live=True))
    monkeypatch.setattr(worker_main, "_configure", lambda settings: None)
    scheduler = _FakeScheduler()
    monkeypatch.setattr(worker_main, "build_scheduler", lambda *a, **k: scheduler)

    worker_main.main()

    assert scheduler.started
