"""Protect understandable dependency boundaries without dictating the file tree.

The old checker assigned every package a number and banned imports between
packages on the same row. That made the diagram tidy while forcing one feature
to scatter its work across orchestration layers. This checker protects the
boundaries a maintainer actually relies on instead: no cycles, no dependency on
the command line, pure records stay free of I/O, and providers never reach into
workflows or reports.

Usage: python scripts/check-imports.py
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from importlib.util import resolve_name
from pathlib import Path

PACKAGE_NAME = "auction_lens"
PACKAGE = Path("src") / PACKAGE_NAME

# An __init__ file is a signpost, not a second place to discover an API.
EMPTY_PACKAGE_MARKERS = {
    "config",
    "history",
    "listings",
    "matching",
    "pricing",
    "providers",
    "providers.nellis",
    "reports",
    "watchlist",
}

IO_OWNERS = {
    "cli",
    "collect",
    "config",
    "daily",
    "doctor",
    "history",
    "providers",
    "reports",
    "setup",
    "watchlist",
}
PURE_RECORD_MODULES = {
    "listings.conditions",
    "listings.model",
    "matching.model",
    "pricing.model",
    "reports.records",
    "watchlist.model",
}
PROVIDER_FORBIDDEN = {
    "cli",
    "collect",
    "daily",
    "doctor",
    "history",
    "matching",
    "pricing",
    "reports",
    "setup",
    "watchlist",
}


def module_name(path: Path) -> str:
    """The importable name represented by one Python source file."""
    relative = path.relative_to(PACKAGE).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join((PACKAGE_NAME, *parts)) if parts else PACKAGE_NAME


def package_of(path: Path) -> str:
    """The package Python uses to resolve a relative import in this file."""
    module = module_name(path)
    return module if path.stem == "__init__" else module.rpartition(".")[0]


def project_imports(path: Path) -> set[str]:
    """Every Auction Lens module named directly by this file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()

    class ImportVisitor(ast.NodeVisitor):
        """Collect runtime imports while ignoring type-checker-only edges."""

        def visit_If(self, node: ast.If) -> None:
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                for statement in node.orelse:
                    self.visit(statement)
                return
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            imported.update(
                alias.name for alias in node.names if alias.name.startswith(f"{PACKAGE_NAME}.")
            )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level:
                relative = "." * node.level + (node.module or "")
                base = resolve_name(relative, package_of(path))
            elif node.module and node.module.startswith(f"{PACKAGE_NAME}."):
                base = node.module
            else:
                return
            imported.add(base)
            imported.update(
                f"{base}.{alias.name}" for alias in node.names if alias.name != "*"
            )

    ImportVisitor().visit(tree)
    return imported


def owner(module: str) -> str:
    """The first feature directory beneath auction_lens."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def existing_target(name: str, modules: set[str]) -> str | None:
    """Resolve an imported symbol path to the nearest source module we own."""
    candidate = name
    while candidate.startswith(PACKAGE_NAME):
        if candidate in modules:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return None


def cycle_in(graph: dict[str, set[str]]) -> list[str]:
    """Return one dependency cycle, or an empty list when the graph is acyclic."""
    visiting: list[str] = []
    active: set[str] = set()
    finished: set[str] = set()

    def visit(module: str) -> list[str]:
        if module in finished:
            return []
        if module in active:
            start = visiting.index(module)
            return [*visiting[start:], module]
        active.add(module)
        visiting.append(module)
        for dependency in sorted(graph[module]):
            found = visit(dependency)
            if found:
                return found
        visiting.pop()
        active.remove(module)
        finished.add(module)
        return []

    for module in sorted(graph):
        found = visit(module)
        if found:
            return found
    return []


def complaints(paths: list[Path]) -> list[str]:
    """Explain every dependency that crosses a human-facing boundary."""
    by_module = {module_name(path): path for path in paths}
    modules = set(by_module)
    graph: dict[str, set[str]] = defaultdict(set)
    failures: list[str] = []

    for module, path in by_module.items():
        direct = project_imports(path)
        graph[module] = {
            target
            for imported in direct
            if (target := existing_target(imported, modules)) is not None and target != module
        }
        short = module.removeprefix(f"{PACKAGE_NAME}.")
        source_owner = owner(module)
        imported_owners = {owner(imported) for imported in direct}

        imports_cli = "cli" in imported_owners
        if source_owner != "cli" and module != f"{PACKAGE_NAME}.__main__" and imports_cli:
            failures.append(f"{path}: only the console entry point may import the CLI")
        if source_owner == "providers":
            for forbidden in sorted(imported_owners & PROVIDER_FORBIDDEN):
                failures.append(f"{path}: provider code must not import '{forbidden}'")
        if short in PURE_RECORD_MODULES:
            for forbidden in sorted(imported_owners & IO_OWNERS):
                failures.append(
                    f"{path}: record module must not import I/O owner '{forbidden}'"
                )
        if path.stem == "__init__" and short in EMPTY_PACKAGE_MARKERS:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in tree.body):
                failures.append(f"{path}: package marker must not hide implementation imports")

    found_cycle = cycle_in(graph)
    if found_cycle:
        failures.append("project import cycle: " + " -> ".join(found_cycle))
    return failures


def main() -> int:
    """Check all project source modules and return a shell-friendly status."""
    failures = complaints(sorted(PACKAGE.rglob("*.py")))
    for failure in failures:
        print(failure)
    if failures:
        print(f"[X] {len(failures)} project import boundary problem(s)")
        return 1
    print("[OK] project imports are acyclic and follow the feature boundaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
