"""A deliberately small path language for reading configured JSON responses.

It supports exactly one thing: walking object keys and list indexes, as in
``results.0.price.value``. Anything more expressive would be a query language
in a configuration file, which is where reviewability goes to die.
"""

from __future__ import annotations

from typing import Any

SEPARATOR = "."


def read_path(value: Any, path: str) -> Any:
    """Follow a dotted path; an empty path means the value itself."""
    if not path:
        return value
    current = value
    for part in path.split(SEPARATOR):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def read_optional_path(value: Any, path: Any, default: Any) -> Any:
    """Read a path that configuration may leave unset."""
    if path is None or path == "":
        return default
    return read_path(value, str(path))
