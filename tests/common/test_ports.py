"""Conformance and zero-infrastructure tests for the §4.5 ports."""

import subprocess
import sys
from datetime import UTC, datetime

from pythia.common import (
    AuditEvent,
    AuditPort,
    Evidence,
    EvidencePort,
    PaperLedgerPort,
    PaperTrade,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)


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
    port.append(AuditEvent(kind="opened", payload={}))


def test_contracts_import_no_infrastructure() -> None:
    code = (
        "import sys, pythia.common\n"
        "blocked = {'sqlalchemy', 'psycopg', 'alembic'} & set(sys.modules)\n"
        "assert not blocked, blocked\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
