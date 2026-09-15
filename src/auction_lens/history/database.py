"""The SQLite database that remembers listings between runs.

Runs are short and single-process, so holding a connection open would buy
nothing and would keep the ignored database file locked between commands.

Money columns are text because exact decimal values must survive a round trip;
SQLite's REAL type would reintroduce binary rounding.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    current_bid TEXT NOT NULL,
    estimated_retail TEXT,
    bid_count INTEGER NOT NULL,
    ends_at TEXT,
    location TEXT NOT NULL,
    conditions TEXT NOT NULL,
    image_url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, listing_id)
);

CREATE TABLE IF NOT EXISTS price_history (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    current_bid TEXT NOT NULL,
    bid_count INTEGER NOT NULL,
    UNIQUE (source, listing_id, observed_at)
);

CREATE TABLE IF NOT EXISTS logistics_decisions (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('feasible', 'infeasible')),
    added_cost TEXT NOT NULL,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, listing_id)
);
"""


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
