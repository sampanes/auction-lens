"""Loading local secrets from an ignored KEY=VALUE file.

This is deliberately not a general dotenv implementation: it exists so a
scheduled task can find SMTP credentials without them being committed, and it
never overrides a value the surrounding process already set.
"""

from __future__ import annotations

import os
from pathlib import Path

COMMENT_PREFIX = "#"
ASSIGNMENT = "="
QUOTES = "\"'"


def load_env_file(path: str | Path) -> None:
    """Set any variable the file defines that the environment does not already."""
    source = Path(path)
    if not source.exists():
        return
    for number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(COMMENT_PREFIX):
            continue
        if ASSIGNMENT not in line:
            raise ValueError(f"invalid environment line {number} in {source}")
        key, value = (part.strip() for part in line.split(ASSIGNMENT, 1))
        value = _unquoted(value)
        if key and value:
            os.environ.setdefault(key, value)


def _unquoted(value: str) -> str:
    """Drop one matching pair of surrounding quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
        return value[1:-1]
    return value
