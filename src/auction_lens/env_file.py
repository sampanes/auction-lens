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


def write_settings(path: str | Path, settings: dict[str, str]) -> None:
    """Set each named value in the file, leaving every other line untouched.

    The file is hand-written and full of comments explaining which variable is
    for what, so this rewrites the assignments in place rather than regenerating
    the file. A name the file does not mention yet is appended.
    """
    target = Path(path)
    lines = target.read_text(encoding="utf-8").splitlines()
    remaining = dict(settings)
    for index, raw_line in enumerate(lines):
        key = _assigned_name(raw_line)
        if key in remaining:
            lines[index] = f"{key}{ASSIGNMENT}{remaining.pop(key)}"
    lines.extend(f"{key}{ASSIGNMENT}{value}" for key, value in remaining.items())
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _assigned_name(raw_line: str) -> str:
    """The variable a line assigns, or empty for a comment or a blank line."""
    line = raw_line.strip()
    if not line or line.startswith(COMMENT_PREFIX) or ASSIGNMENT not in line:
        return ""
    return line.split(ASSIGNMENT, 1)[0].strip()


def _unquoted(value: str) -> str:
    """Drop one matching pair of surrounding quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
        return value[1:-1]
    return value
