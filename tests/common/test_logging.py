"""Tests for the observability backbone (§3.10, §8.4): structlog config + correlation IDs."""

import json
import subprocess
import sys
from collections.abc import Iterator

import pytest
import structlog

from pythia.common.logging import (
    bind,
    bind_correlation,
    clear_correlation,
    current_correlation_id,
)


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    clear_correlation()


def test_correlation_id_roundtrips_and_clears() -> None:
    assert current_correlation_id() is None
    bind_correlation("run-123")
    assert current_correlation_id() == "run-123"
    clear_correlation()
    assert current_correlation_id() is None


def test_bind_adds_field_without_dropping_correlation() -> None:
    bind_correlation("run-9", market_id="m1")
    bind(estimate_id="e1")
    bound = structlog.contextvars.get_contextvars()
    assert bound["correlation_id"] == "run-9"
    assert bound["market_id"] == "m1"
    assert bound["estimate_id"] == "e1"


def _run_logger_snippet(snippet: str) -> list[dict[str, object]]:
    result = subprocess.run(
        [sys.executable, "-c", snippet], capture_output=True, text=True, check=True
    )
    records: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def test_emits_json_line_with_standard_fields_and_propagated_context() -> None:
    snippet = (
        "from pythia.common.logging import configure_logging, get_logger, bind_correlation, bind\n"
        "configure_logging(service='test-svc')\n"
        "bind_correlation('run-123', market_id='m1')\n"
        "bind(estimate_id='e1')\n"
        "get_logger('pythia.test').info('hello world')\n"
    )
    records = _run_logger_snippet(snippet)
    record = next(r for r in records if r.get("event") == "hello world")
    assert record["service"] == "test-svc"
    assert record["level"] == "info"
    assert record["correlation_id"] == "run-123"
    assert record["market_id"] == "m1"
    assert record["estimate_id"] == "e1"
    assert "timestamp" in record


def test_console_renderer_emits_non_json_when_json_logs_false() -> None:
    snippet = (
        "from pythia.common.logging import configure_logging, get_logger\n"
        "configure_logging(service='dev-svc', json_logs=False)\n"
        "get_logger('pythia.test').info('console line')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", snippet], capture_output=True, text=True, check=True
    )
    assert "console line" in result.stdout
    assert _run_logger_snippet(snippet) == []
