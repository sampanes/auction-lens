"""The SQLite tables Auction Lens keeps between runs.

Money is stored as text so that exact decimal values survive a round trip;
SQLite's REAL type would reintroduce the binary rounding the model avoids.
"""

from __future__ import annotations

LISTINGS_TABLE = """
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
"""

PRICE_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS price_history (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    current_bid TEXT NOT NULL,
    bid_count INTEGER NOT NULL,
    UNIQUE (source, listing_id, observed_at)
);
"""

LOGISTICS_DECISIONS_TABLE = """
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

SCHEMA = LISTINGS_TABLE + PRICE_HISTORY_TABLE + LOGISTICS_DECISIONS_TABLE
