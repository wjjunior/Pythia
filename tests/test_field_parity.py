"""Contract-parity guard (§3, PYT-129): the common↔db dual-type seam stays reconciled.

`common/types.py` carries frozen Pydantic domain contracts; `db/models.py` carries SQLModel
``table=True`` rows. They are deliberately separate same-named families (see ARCHITECTURE.md, "The
dual-type seam") — the storage layer adds surrogate PKs, timestamps, pgvector/JSONB columns and
denormalized query tags, and a few contract fields live in a child table. This test pins those
intentional divergences so a field added to one side and not the other — the silent contract drift
CLAUDE.md rule 13 warns about — fails the build.
"""

import inspect

import pytest
from pydantic import BaseModel
from sqlmodel import SQLModel

import pythia.common.types as common_types
import pythia.db.models as db_models

# Per pair: (db_only, common_only) field names that intentionally diverge, with the reason.
# Everything NOT listed must be present on both sides; an undocumented diff fails the test.
_ALLOWED_DIVERGENCE: dict[str, tuple[set[str], set[str]]] = {
    # Surrogate PK on append-only / secondary tables (the contract is keyed elsewhere).
    "AuditEvent": ({"id"}, set()),
    # Surrogate PK + storage timestamp + denormalized FK tags for cost queries.
    "CostEvent": ({"id", "ts", "estimate_id", "market_id", "pipeline_run_id"}, set()),
    "Score": ({"id", "created_at"}, set()),
    # Storage timestamps the domain contract doesn't carry.
    "Market": ({"created_at"}, set()),
    "Postmortem": ({"created_at"}, set()),
    "VenueQuote": ({"created_at"}, set()),
    "ImprovementCandidate": ({"created_at", "updated_at"}, set()),
    # created_at (storage) + members, which live in the estimate_members child table, not a column.
    "Estimate": ({"created_at"}, {"members"}),
    # Storage-specific columns: the pgvector embedding + JSONB metadata, and the raw provider JSON.
    "Evidence": ({"embedding", "metadata"}, set()),
    "MarketSnapshot": ({"raw"}, set()),
}


def _common_models() -> dict[str, type[BaseModel]]:
    return {
        name: obj
        for name, obj in vars(common_types).items()
        if inspect.isclass(obj)
        and issubclass(obj, BaseModel)
        and obj.__module__ == common_types.__name__
        and not name.startswith("_")
    }


def _db_tables() -> dict[str, type[SQLModel]]:
    return {
        name: obj
        for name, obj in vars(db_models).items()
        if inspect.isclass(obj)
        and issubclass(obj, SQLModel)
        and getattr(obj, "__tablename__", None) is not None
    }


def _db_columns(model: type[SQLModel]) -> set[str]:
    # The real schema column names (e.g. Evidence stores `metadata`, whose Python field is `meta`);
    # SQLModel table classes carry __table__ at runtime, which the type stub does not expose.
    table = model.__table__  # type: ignore[attr-defined]
    return {column.name for column in table.columns}


_PAIRS = sorted(set(_common_models()) & set(_db_tables()))


def test_the_allowlist_has_no_stale_entries() -> None:
    # Every documented divergence must reference an actual common↔db pair, so a removed pair can't
    # leave a stale exemption that hides a future drift.
    assert set(_ALLOWED_DIVERGENCE) <= set(_PAIRS)


@pytest.mark.parametrize("name", _PAIRS)
def test_common_and_db_fields_stay_reconciled(name: str) -> None:
    common_fields = set(_common_models()[name].model_fields)
    db_fields = _db_columns(_db_tables()[name])
    allowed_db_only, allowed_common_only = _ALLOWED_DIVERGENCE.get(name, (set(), set()))

    undocumented_db_only = (db_fields - common_fields) - allowed_db_only
    undocumented_common_only = (common_fields - db_fields) - allowed_common_only

    assert undocumented_db_only == set(), (
        f"{name}: db column(s) {sorted(undocumented_db_only)} have no contract field — add it to "
        "common/types.py or record the divergence in _ALLOWED_DIVERGENCE"
    )
    assert undocumented_common_only == set(), (
        f"{name}: contract field(s) {sorted(undocumented_common_only)} have no db column — add to "
        "db/models.py or record the divergence in _ALLOWED_DIVERGENCE"
    )
