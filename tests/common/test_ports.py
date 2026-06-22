"""Conformance and zero-infrastructure tests for the §4.5 ports."""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pythia.common import (
    AuditEvent,
    AuditKind,
    AuditPort,
    Evidence,
    EvidencePort,
    ExecutionPort,
    PaperLedgerPort,
    PaperTrade,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)
_SRC = Path(__file__).resolve().parents[2] / "src" / "pythia"


class _StubEvidence:
    def retrieve(self, market_id: str, asof: datetime, k: int) -> list[Evidence]:
        return []


class _StubLedger:
    def open(self, trade: PaperTrade) -> PaperTrade:
        return trade

    def resolve(self, market_id: str, outcome: int, at: datetime) -> None:
        return None


class _StubAudit:
    def append(self, event: AuditEvent) -> None:
        return None


def test_evidence_stub_conforms_and_requires_asof() -> None:
    port: EvidencePort = _StubEvidence()
    assert port.retrieve("m1", _TS, 5) == []


def test_ledger_stub_conforms() -> None:
    port: PaperLedgerPort = _StubLedger()
    port.resolve("m1", 1, _TS)


def test_audit_stub_conforms() -> None:
    port: AuditPort = _StubAudit()
    port.append(AuditEvent(kind=AuditKind.ESTIMATE, payload={}))


def test_execution_port_is_an_abstract_seam_with_no_instance() -> None:
    with pytest.raises(TypeError):
        ExecutionPort()  # type: ignore[misc]  # a Protocol seam, never instantiated


def test_execution_port_stays_unimplemented_with_no_caller() -> None:
    allowed = {_SRC / "common" / "ports.py", _SRC / "common" / "__init__.py"}
    leaks = sorted(
        str(path.relative_to(_SRC))
        for path in _SRC.rglob("*.py")
        if path not in allowed and "ExecutionPort" in path.read_text()
    )
    assert leaks == [], f"ExecutionPort must stay unimplemented until the calibration gate: {leaks}"


def test_contracts_import_no_infrastructure() -> None:
    code = (
        "import sys, pythia.common\n"
        "blocked = {'sqlalchemy', 'psycopg', 'alembic'} & set(sys.modules)\n"
        "assert not blocked, blocked\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
