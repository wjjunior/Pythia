"""Phase 1 smoke: the package and its process entrypoints import cleanly."""

import importlib

import pythia


def test_version_is_exposed() -> None:
    assert pythia.__version__ == "0.1.0"


def test_entrypoints_import() -> None:
    for module in ("pythia.api.__main__", "pythia.worker.__main__"):
        assert importlib.import_module(module) is not None
