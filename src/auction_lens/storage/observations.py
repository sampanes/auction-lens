"""Remembering listings between runs so a report can say what changed."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..fields import LABEL_SEPARATOR
from ..models import Listing, ObservationChange
from .database import Database

_SELECT_PREVIOUS_BID = """
SELECT current_bid FROM listings WHERE source = ? AND listing_id = ?
"""

_UPSERT_LISTING = """
INSERT INTO listings (
    source, listing_id, title, url, current_bid, estimated_retail,
    bid_count, ends_at, location, conditions, image_url, first_seen, last_seen
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source, listing_id) DO UPDATE SET
    title=excluded.title,
    url=excluded.url,
    current_bid=excluded.current_bid,
    estimated_retail=excluded.estimated_retail,
    bid_count=excluded.bid_count,
    ends_at=excluded.ends_at,
    location=excluded.location,
    conditions=excluded.conditions,
    image_url=excluded.image_url,
    last_seen=excluded.last_seen
"""

_INSERT_PRICE_POINT = """
INSERT OR IGNORE INTO price_history (
    source, listing_id, observed_at, current_bid, bid_count
) VALUES (?, ?, ?, ?, ?)
"""


@dataclass(frozen=True)
class ObservationStore:
    """Listing state and its price history."""

    database: Database

    def observe(self, listing: Listing) -> ObservationChange:
        """Record one sighting and report how it differs from the last one."""
        observed_at = listing.observed_at.isoformat()
        with self.database.connect() as connection:
            previous = connection.execute(
                _SELECT_PREVIOUS_BID, (listing.source, listing.listing_id)
            ).fetchone()
            previous_bid = Decimal(previous[0]) if previous else None
            connection.execute(_UPSERT_LISTING, _listing_row(listing, observed_at))
            connection.execute(
                _INSERT_PRICE_POINT,
                (
                    listing.source,
                    listing.listing_id,
                    observed_at,
                    str(listing.current_bid),
                    listing.bid_count,
                ),
            )
        return ObservationChange(
            is_new=previous is None,
            price_changed=previous_bid is not None and previous_bid != listing.current_bid,
            previous_bid=previous_bid,
        )


def _listing_row(listing: Listing, observed_at: str) -> tuple:
    """Flatten a listing for storage; first_seen only survives on an insert."""
    return (
        listing.source,
        listing.listing_id,
        listing.title,
        listing.url,
        str(listing.current_bid),
        None if listing.estimated_retail is None else str(listing.estimated_retail),
        listing.bid_count,
        None if listing.ends_at is None else listing.ends_at.isoformat(),
        listing.location,
        LABEL_SEPARATOR.join(listing.conditions),
        # One representative image is enough to notice a lot changed; the
        # whole gallery is kept in the watchlist, which a person reads.
        listing.condition_photo_url,
        observed_at,
        observed_at,
    )
