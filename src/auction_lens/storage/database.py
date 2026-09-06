"""One SQLite file, opened per operation.

Runs are short and single-process, so holding a connection open would buy
nothing and would keep the ignored database file locked between commands.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from .schema import SCHEMA


@dataclass(frozen=True)
class Database:
    """The location of the observation database and how to talk to it."""

    path: Path

    @classmethod
    def at(cls, path: str | Path) -> Database:
        return cls(Path(path))

    def initialize(self) -> None:
        """Create the parent directory and any missing tables."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection whose work commits together, or not at all."""
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                yield connection
