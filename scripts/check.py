"""Run the complete local and CI validation suite from one maintained list."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The browser companion is JavaScript, and it is the half that decides whether
# any Vine record exists at all. Its own suite needs Node, which is the only
# program here this interpreter cannot be. Nothing is installed for it: there
# is no package.json and no lockfile in this repository, so the check uses
# Node's own modules and no published package can reach it.
NODE = "node"

# Each check names the program that runs it; None means this interpreter.
CHECKS = (
    (None, "compiling source and tests", ("-m", "compileall", "-q", "src", "tests")),
    (None, "checking tracked text files", ("scripts/check-ascii.py",)),
    (None, "checking module layering", ("scripts/check-imports.py",)),
    (None, "linting", ("-m", "ruff", "check", "src", "tests", "scripts")),
    (None, "running tests", ("-m", "unittest", "discover", "-s", "tests", "-v")),
    (NODE, "checking the browser companion", ("scripts/vine/test-extension.js",)),
)


def program_for(interpreter: str | None) -> str | None:
    """Where to find one check's program, or None when it is not installed."""
    if interpreter is None:
        return sys.executable
    return shutil.which(interpreter)


def main() -> int:
    """Stop at the first failed check and preserve that command's exit code."""
    total = len(CHECKS)
    for index, (interpreter, label, arguments) in enumerate(CHECKS, start=1):
        print(f"[{index}/{total}] {label}", flush=True)
        program = program_for(interpreter)
        if program is None:
            # Skipping it would leave that half unchecked while this script
            # still reported success, which is the one answer a gate must
            # never give. Saying what is missing costs a line.
            print(f"[X] {label} needs {interpreter}, which is not on PATH")
            print(f"    install {interpreter}; the check it runs is {arguments[0]}")
            return 1
        result = subprocess.run((program, *arguments), cwd=ROOT, check=False)
        if result.returncode:
            print(f"[X] {label} failed")
            return result.returncode
    print("[OK] every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
