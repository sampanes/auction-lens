"""Loading local secrets from an ignored KEY=VALUE file.

This is deliberately not a general dotenv implementation: it exists so a
scheduled task can find SMTP credentials without them being committed, and it
never overrides a value the surrounding process already set.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

COMMENT_PREFIX = "#"
ASSIGNMENT = "="


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
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    remaining = dict(settings)
    for index, raw_line in enumerate(lines):
        key = _assigned_name(raw_line)
        if key in remaining:
            lines[index] = f"{key}{ASSIGNMENT}{_encoded(remaining.pop(key))}"
    lines.extend(
        f"{key}{ASSIGNMENT}{_encoded(value)}" for key, value in remaining.items()
    )
    _write_atomically(target, ("\n".join(lines) + "\n").encode("utf-8"))


def _assigned_name(raw_line: str) -> str:
    """The variable a line assigns, or empty for a comment or a blank line."""
    line = raw_line.strip()
    if not line or line.startswith(COMMENT_PREFIX) or ASSIGNMENT not in line:
        return ""
    return line.split(ASSIGNMENT, 1)[0].strip()


def _unquoted(value: str) -> str:
    """Remove one matching quote pair without changing legacy contents."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _encoded(value: str) -> str:
    """Represent one exact single-line value without changing its contents."""
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("environment settings must be single-line text")
    # This loader treats only the first and last quote as syntax, so an inner
    # quote or backslash remains literal and no legacy escape convention is
    # silently introduced.
    return f"'{value}'"


def _write_atomically(path: Path, value: bytes) -> None:
    """Replace a settings file only after its complete successor is durable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name, suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
