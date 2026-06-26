"""Layering + money-path purity invariants (§3, PYT-108/PYT-110): the pure core decides size/veto
and must stay deterministic and dependency-free.

Two AST guards over real (non-TYPE_CHECKING) runtime code in scoring/risk/eval/portfolio:
  1. an import allowlist — only stdlib, the math stack (numpy/scipy/pydantic), the shared contracts
     (pythia.common) and the sibling pure packages. A reverse import (pythia.agents/db/rag) or a
     framework (fastapi/httpx/sqlmodel) fails CI — "math decides", never an agent;
  2. a clock/RNG ban — datetime.now/utcnow, time.time and the stdlib random module are forbidden, so
     time and seeds enter as parameters. np.random.default_rng(seed) (seeded ⇒ reproducible) and an
     injected time.monotonic duration clock stay legal.

The pure packages take config as a *parameter*, so TYPE_CHECKING-only imports are not runtime
coupling and are excluded — only real, runtime imports count.
"""

import ast
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "pythia"

# The pure money-path + analytics core (CLAUDE.md rule 16).
_PURE_CORE = ("scoring", "risk", "eval", "portfolio")

# Allowlist for the pure core: the math stack, the shared contracts, and the sibling pure packages.
_ALLOWED_THIRD_PARTY = frozenset({"numpy", "scipy", "pydantic"})
_ALLOWED_PYTHIA = ("pythia.common", *(f"pythia.{pkg}" for pkg in _PURE_CORE))

# Reverse/lateral guards for non-pure packages (an adapter/config must not reach into the core).
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "db": ("pythia.scoring",),
    "config": ("pythia.eval",),
}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_imports(path: Path) -> set[str]:
    modules: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                for sibling in child.orelse:
                    visit(sibling)
                continue
            if isinstance(child, ast.Import):
                modules.update(alias.name for alias in child.names)
            elif isinstance(child, ast.ImportFrom) and child.module and child.level == 0:
                modules.add(child.module)
            visit(child)

    visit(ast.parse(path.read_text(), filename=str(path)))
    return modules


def _import_allowed(module: str) -> bool:
    """True if a pure-core module may import ``module`` at runtime."""
    top = module.split(".")[0]
    if top == "pythia":
        return any(module == a or module.startswith(f"{a}.") for a in _ALLOWED_PYTHIA)
    return top in sys.stdlib_module_names or top in _ALLOWED_THIRD_PARTY


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _scan_clock_rng(tree: ast.AST, label: str) -> list[str]:
    """Wall clock or stdlib RNG in the tree (np.random(seed) and time.monotonic stay legal)."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [
                f"{label} imports {a.name}" for a in node.names if a.name.split(".")[0] == "random"
            ]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] == "random":
                found.append(f"{label} imports from {node.module}")
        elif isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted is not None and (
                dotted.split(".")[0] == "random"
                or dotted == "time.time"
                or dotted in ("datetime.now", "datetime.utcnow")
                or dotted.endswith((".datetime.now", ".datetime.utcnow"))
            ):
                found.append(f"{label} uses {dotted}")
    return found


def _clock_rng_violations(path: Path) -> list[str]:
    source = path.read_text()
    return _scan_clock_rng(ast.parse(source, filename=str(path)), str(path.relative_to(_SRC)))


def _denylist_violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for path in sorted((_SRC / package).rglob("*.py")):
        for module in _runtime_imports(path):
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden):
                found.append(f"{path.relative_to(_SRC)} imports {module}")
    return found


# ---- The real packages on main: every guard must be green ----


@pytest.mark.parametrize("package", _PURE_CORE)
def test_pure_core_imports_stay_within_the_allowlist(package: str) -> None:
    bad = [
        f"{path.relative_to(_SRC)} imports {module}"
        for path in sorted((_SRC / package).rglob("*.py"))
        for module in _runtime_imports(path)
        if not _import_allowed(module)
    ]
    assert bad == []


@pytest.mark.parametrize("package", _PURE_CORE)
def test_pure_core_has_no_clock_or_rng(package: str) -> None:
    bad = [
        v for path in sorted((_SRC / package).rglob("*.py")) for v in _clock_rng_violations(path)
    ]
    assert bad == []


@pytest.mark.parametrize(("package", "forbidden"), sorted(_FORBIDDEN.items()))
def test_should_respect_the_layering_dependency_rule(
    package: str, forbidden: tuple[str, ...]
) -> None:
    assert _denylist_violations(package, forbidden) == []


# ---- The guards themselves: red on an injected violation, allowing the legitimate patterns ----


@pytest.mark.parametrize(
    "module",
    [
        "pythia.agents",
        "pythia.agents.estimation",
        "pythia.db",
        "pythia.rag",
        "pythia.ingestion",
        "pythia.config",
        "pythia.api",
        "fastapi",
        "httpx",
        "sqlmodel",
    ],
)
def test_allowlist_rejects_money_path_violations(module: str) -> None:
    assert _import_allowed(module) is False


@pytest.mark.parametrize(
    "module",
    [
        "math",
        "datetime",
        "collections.abc",
        "numpy",
        "numpy.typing",
        "scipy.optimize",
        "pydantic",
        "pythia.common",
        "pythia.common.types",
        "pythia.scoring.functions",
        "pythia.risk",
        "pythia.eval.cohorts",
        "pythia.portfolio.functions",
    ],
)
def test_allowlist_accepts_pure_dependencies(module: str) -> None:
    assert _import_allowed(module) is True


@pytest.mark.parametrize(
    "snippet",
    [
        "from datetime import datetime\nx = datetime.now()\n",
        "import datetime\nx = datetime.datetime.utcnow()\n",
        "import time\nx = time.time()\n",
        "import random\nx = random.random()\n",
        "from random import random\nx = random()\n",
    ],
)
def test_clock_rng_ban_flags_nondeterminism(snippet: str) -> None:
    assert _scan_clock_rng(ast.parse(snippet), "probe.py") != []


@pytest.mark.parametrize(
    "snippet",
    [
        "import numpy as np\nrng = np.random.default_rng(seed)\n",  # seeded ⇒ reproducible
        "import time\nclock = time.monotonic\n",  # injected duration clock, not the wall clock
        "from datetime import datetime\nasof: datetime\n",  # datetime as a parameter type only
    ],
)
def test_clock_rng_ban_allows_deterministic_patterns(snippet: str) -> None:
    assert _scan_clock_rng(ast.parse(snippet), "probe.py") == []
