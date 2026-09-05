"""Small-file reads and writes that survive an interrupted run.

Caches and ledgers are written while the network is in play, so every write
lands in a temporary file first and is then renamed over the target. A reader
therefore sees either the previous file or the complete new one, never a
half-written mixture of the two.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

JSON_INDENT = 2
TEMPORARY_SUFFIX = ".tmp"


def read_json(path: Path, *, default: Any) -> Any:
    """Read a JSON file, or return the default when it does not exist yet."""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomically(path: Path, value: Any) -> None:
    text = json.dumps(value, indent=JSON_INDENT, ensure_ascii=False) + "\n"
    write_bytes_atomically(path, text.encode("utf-8"))


def write_bytes_atomically(path: Path, value: bytes) -> None:
    """Write bytes through a temporary file in the destination directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name, suffix=TEMPORARY_SUFFIX, dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        _discard(temporary_name)
        raise


def _discard(name: str) -> None:
    try:
        os.unlink(name)
    except FileNotFoundError:
        pass
