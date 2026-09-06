"""Fail if any module imports something it is not allowed to depend on.

docs/ARCHITECTURE.md describes a one-way flow of dependencies. A document
cannot enforce itself, so the same rule is written below as data and checked on
every run. Without this, one convenient import inverts the architecture and
nothing notices until the code is hard to change again.

The rule: a module may import a module in a LOWER layer, or another module in
its own package. It may not import a peer in the same layer, and it may never
import upward.

Usage: python scripts/check-imports.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = Path("src/auction_lens")

# Lowest first. Everything on one line is a peer: peers must not know about
# each other, which is what keeps them independently readable and testable.
LAYERS = (
    ("fields",),
    ("grading",),
    ("env_file", "file_io", "models"),
    ("config",),
    ("logistics",),
    ("acquisition", "ingest", "reporting", "scoring", "storage", "valuation"),
    ("pipeline",),
    ("cli",),
)

LAYER_OF = {name: rank for rank, names in enumerate(LAYERS) for name in names}


def owner_of(path: Path) -> str:
    """The top-level module or package a file belongs to."""
    parts = path.relative_to(PACKAGE).parts
    return parts[0] if len(parts) > 1 else path.stem


def imported_names(path: Path, owner: str) -> set[str]:
    """Every top-level project module this file imports, by name."""
    depth = len(path.relative_to(PACKAGE).parts) - 1
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        # "from .x import y" inside a package stays inside that package;
        # "from ..x import y" leaves it, and so does "from .x" at the top level.
        leaves_the_package = node.level > 1 or depth == 0
        if leaves_the_package and node.module:
            name = node.module.split(".")[0]
            if name != owner:
                found.add(name)
    return found


def complaints(path: Path) -> list[str]:
    owner = owner_of(path)
    if owner not in LAYER_OF:
        return [f"{path}: '{owner}' is not listed in LAYERS; add it to this script"]
    found = []
    for name in sorted(imported_names(path, owner)):
        if name not in LAYER_OF:
            found.append(f"{path}: imports unknown module '{name}'")
        elif LAYER_OF[name] > LAYER_OF[owner]:
            found.append(f"{path}: '{owner}' must not import upward from '{name}'")
        elif LAYER_OF[name] == LAYER_OF[owner]:
            found.append(f"{path}: '{owner}' and '{name}' are peers and must stay apart")
    return found


def main() -> int:
    failures = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.stem in {"__init__", "__main__"} and path.parent == PACKAGE:
            continue
        failures.extend(complaints(path))
    for line in failures:
        print(line)
    if failures:
        print(f"[X] {len(failures)} import(s) break the layering in docs/ARCHITECTURE.md")
        return 1
    print("[OK] every import follows the layering in docs/ARCHITECTURE.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
