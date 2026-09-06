"""Run the complete local and CI validation suite from one maintained list."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHECKS = (
    ("compiling source and tests", ("-m", "compileall", "-q", "src", "tests")),
    ("checking tracked text files", ("scripts/check-ascii.py",)),
    ("checking module layering", ("scripts/check-imports.py",)),
    ("linting", ("-m", "ruff", "check", "src", "tests", "scripts")),
    ("running tests", ("-m", "unittest", "discover", "-s", "tests", "-v")),
)


def main() -> int:
    """Stop at the first failed check and preserve that command's exit code."""
    total = len(CHECKS)
    for index, (label, arguments) in enumerate(CHECKS, start=1):
        print(f"[{index}/{total}] {label}", flush=True)
        result = subprocess.run((sys.executable, *arguments), cwd=ROOT, check=False)
        if result.returncode:
            print(f"[X] {label} failed")
            return result.returncode
    print("[OK] every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
