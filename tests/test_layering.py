"""Layering invariants (§3, PYT-108): the pure core and the dependency rule, enforced.

The pure packages take config as a *parameter*, so TYPE_CHECKING-only imports (type hints) are
not runtime coupling and are excluded — only real, runtime imports count.
"""

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "pythia"

_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "eval": ("sqlmodel", "pythia.db", "pythia.config"),
    "portfolio": ("sqlmodel", "pythia.db", "pythia.config"),
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


def _violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for path in sorted((_SRC / package).rglob("*.py")):
        for module in _runtime_imports(path):
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden):
                found.append(f"{path.relative_to(_SRC)} imports {module}")
    return found


@pytest.mark.parametrize(("package", "forbidden"), sorted(_FORBIDDEN.items()))
def test_should_respect_the_layering_dependency_rule(
    package: str, forbidden: tuple[str, ...]
) -> None:
    assert _violations(package, forbidden) == []
