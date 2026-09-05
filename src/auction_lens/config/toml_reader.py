"""A small typed reader over one TOML table.

Configuration is the part of this project an operator edits by hand, so every
coercion reports the full key path that has to change. Keeping the coercions
here also keeps the section loaders free of ``str(table.get(...))`` noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


@dataclass(frozen=True)
class Section:
    """One TOML table plus the dotted path that leads to it."""

    data: Mapping[str, Any]
    path: str = ""

    def contains(self, key: str) -> bool:
        return key in self.data

    def table(self, key: str) -> "Section":
        """Return a nested table, or an empty section when it is absent."""
        value = self.data.get(key, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{self._label(key)} must be a table")
        return Section(value, self._label(key))

    def tables(self, key: str) -> tuple["Section", ...]:
        """Return an array of tables such as ``[[interests]]``."""
        rows = self.data.get(key, [])
        if not isinstance(rows, list):
            raise ValueError(f"{self._label(key)} must be an array of tables")
        sections = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"{self._label(key)}[{index}] must be a table")
            sections.append(Section(row, f"{self._label(key)}[{index}]"))
        return tuple(sections)

    def text(self, key: str, default: str = "") -> str:
        value = self.data.get(key, default)
        return default if value is None else str(value)

    def required_text(self, key: str) -> str:
        value = self.text(key).strip()
        if not value:
            raise ValueError(f"{self._label(key)} is required")
        return value

    def flag(self, key: str, default: bool) -> bool:
        return bool(self.data.get(key, default))

    def integer(self, key: str, default: int) -> int:
        value = self.data.get(key, default)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self._label(key)} must be a whole number") from exc

    def decimal(self, key: str, default: Any) -> Decimal:
        value = self.data.get(key, default)
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{self._label(key)} must be a number") from exc

    def non_negative_decimal(self, key: str, default: Any) -> Decimal:
        """Read a number that would be meaningless below zero, such as a fee."""
        value = self.decimal(key, default)
        if value < 0:
            raise ValueError(f"{self._label(key)} cannot be negative")
        return value

    def optional_decimal(self, key: str) -> Decimal | None:
        return self.decimal(key, 0) if self.contains(key) else None

    def lowercase_texts(self, key: str) -> tuple[str, ...]:
        """Read a list of free-text terms, lowercased for case-insensitive use."""
        values = self.data.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{self._label(key)} must be an array")
        return tuple(str(value).lower() for value in values)

    def integer_map(self, key: str) -> dict[str, int]:
        """Read a table of lowercase labels to whole numbers, such as penalties."""
        table = self.table(key)
        return {str(name).lower(): table.integer(name, 0) for name in table.data}

    def _label(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key
