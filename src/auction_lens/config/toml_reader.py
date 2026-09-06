"""A small typed reader over one TOML table.

Configuration is the part of this project an operator edits by hand, so every
reader reports the full key path that has to change.

TOML is already typed, so this module never coerces: a value that is written as
the wrong kind of thing is an operator mistake worth reporting, not something to
be quietly converted. What a value must *be* once read comes from ``fields``,
which is the same vocabulary listing input is held to.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from ..fields import require_finite, require_not_negative


@dataclass(frozen=True)
class Section:
    """One TOML table plus the dotted path that leads to it."""

    data: Mapping[str, Any]
    path: str = ""

    def contains(self, key: str) -> bool:
        return key in self.data

    def table(self, key: str) -> Section:
        """Return a nested table, or an empty section when it is absent."""
        value = self.data.get(key, {})
        if not isinstance(value, Mapping):
            raise ValueError(f"{self.label(key)} must be a table")
        return Section(value, self.label(key))

    def tables(self, key: str) -> tuple[Section, ...]:
        """Return an array of tables such as ``[[interests]]``."""
        rows = self.data.get(key, [])
        if not isinstance(rows, list):
            raise ValueError(f"{self.label(key)} must be an array of tables")
        sections = []
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"{self.label(key)}[{index}] must be a table")
            sections.append(Section(row, f"{self.label(key)}[{index}]"))
        return tuple(sections)

    def text(self, key: str, default: str = "") -> str:
        value = self.data.get(key, default)
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValueError(f"{self.label(key)} must be text")
        return value

    def required_text(self, key: str) -> str:
        value = self.text(key).strip()
        if not value:
            raise ValueError(f"{self.label(key)} is required")
        return value

    def flag(self, key: str, default: bool) -> bool:
        value = self.data.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"{self.label(key)} must be true or false")
        return value

    def integer(self, key: str, default: int) -> int:
        value = self.data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{self.label(key)} must be a whole number")
        return value

    def non_negative_integer(self, key: str, default: int) -> int:
        """For a table of named numbers, where no record can name the key for us."""
        return require_not_negative(self.integer(key, default), field_name=self.label(key))

    def decimal(self, key: str, default: Any) -> Decimal:
        value = self.data.get(key, default)
        if self.contains(key) and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError(f"{self.label(key)} must be a number")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{self.label(key)} must be a number") from exc
        return require_finite(number, field_name=self.label(key))

    def optional_decimal(self, key: str) -> Decimal | None:
        """Read a number the file is allowed to leave out entirely."""
        return self.decimal(key, 0) if self.contains(key) else None

    def lowercase_texts(self, key: str) -> tuple[str, ...]:
        """Read a list of free-text terms, lowercased for case-insensitive use."""
        values = self.data.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{self.label(key)} must be an array")
        normalized = []
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{self.label(key)}[{index}] must be non-empty text")
            normalized.append(value.strip().lower())
        return tuple(normalized)

    def non_negative_integer_map(self, key: str) -> dict[str, int]:
        """Read named counts or penalties, rejecting values that reverse their meaning."""
        table = self.table(key)
        return {
            str(name).lower(): table.non_negative_integer(name, 0) for name in table.data
        }

    def label(self, key: str) -> str:
        """The dotted path an operator would search their file for."""
        return f"{self.path}.{key}" if self.path else key
