from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import Listing, LogisticsDecision, ObservationChange


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


class ObservationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.executescript(SCHEMA)

    def observe(self, listing: Listing) -> ObservationChange:
        observed_at = listing.observed_at.isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                previous = connection.execute(
                    "SELECT current_bid FROM listings WHERE source = ? AND listing_id = ?",
                    (listing.source, listing.listing_id),
                ).fetchone()
                previous_bid = Decimal(previous[0]) if previous else None
                change = ObservationChange(
                    is_new=previous is None,
                    price_changed=previous_bid is not None and previous_bid != listing.current_bid,
                    previous_bid=previous_bid,
                )
                connection.execute(
                    """
                    INSERT INTO listings (
                        source, listing_id, title, url, current_bid, estimated_retail,
                        bid_count, ends_at, location, conditions, image_url, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, listing_id) DO UPDATE SET
                        title=excluded.title, url=excluded.url, current_bid=excluded.current_bid,
                        estimated_retail=excluded.estimated_retail, bid_count=excluded.bid_count,
                        ends_at=excluded.ends_at, location=excluded.location,
                        conditions=excluded.conditions, image_url=excluded.image_url,
                        last_seen=excluded.last_seen
                    """,
                    (
                        listing.source,
                        listing.listing_id,
                        listing.title,
                        listing.url,
                        str(listing.current_bid),
                        None if listing.estimated_retail is None else str(listing.estimated_retail),
                        listing.bid_count,
                        None if listing.ends_at is None else listing.ends_at.isoformat(),
                        listing.location,
                        "|".join(listing.conditions),
                        listing.image_url,
                        observed_at,
                        observed_at,
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO price_history VALUES (?, ?, ?, ?, ?)",
                    (listing.source, listing.listing_id, observed_at, str(listing.current_bid), listing.bid_count),
                )
        return change

    def get_logistics_decision(self, source: str, listing_id: str) -> LogisticsDecision | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT status, added_cost, note FROM logistics_decisions "
                "WHERE source = ? AND listing_id = ?",
                (source, listing_id),
            ).fetchone()
        if row is None:
            return None
        return LogisticsDecision(status=row[0], added_cost=Decimal(row[1]), note=row[2])

    def set_logistics_decision(
        self,
        source: str,
        listing_id: str,
        decision: LogisticsDecision,
    ) -> None:
        if decision.status not in {"feasible", "infeasible"}:
            raise ValueError("logistics decision must be feasible or infeasible")
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO logistics_decisions (
                        source, listing_id, status, added_cost, note, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, listing_id) DO UPDATE SET
                        status=excluded.status,
                        added_cost=excluded.added_cost,
                        note=excluded.note,
                        updated_at=excluded.updated_at
                    """,
                    (
                        source,
                        listing_id,
                        decision.status,
                        str(decision.added_cost),
                        decision.note,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

    def clear_logistics_decision(self, source: str, listing_id: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM logistics_decisions WHERE source = ? AND listing_id = ?",
                    (source, listing_id),
                )
