"""What lots were last seen going for, for the ones that have since closed.

Nothing new is written here. Every fact this needs was already being recorded:
``listings`` knows when a lot closed, and ``price_history`` knows what it cost
each time it was looked at. Putting those two together is the only step that
was missing, and it is a read, so the answer improves on its own as more looks
accumulate rather than needing a migration.

The one reading that matters is the last one taken *before* the close. A look
taken after a lot ended is not evidence about that auction -- the page may be
stale, or the lot may have been relisted -- so this asks the database for the
last look while the lot was still open, and reports how late that look was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ..models import ClosingPrice
from .database import Database

# Timestamps are stored as ISO-8601 in UTC, and strings in that one shape sort
# the same way the instants do. That is what lets the comparison and the MAX
# below happen in SQLite rather than by reading every row into Python.
_SELECT_CLOSED = """
SELECT
    listing.source,
    listing.listing_id,
    listing.title,
    listing.url,
    listing.estimated_retail,
    listing.ends_at,
    seen.current_bid,
    seen.bid_count,
    seen.observed_at
FROM listings AS listing
JOIN price_history AS seen
    ON seen.source = listing.source
   AND seen.listing_id = listing.listing_id
   AND seen.observed_at = (
       SELECT MAX(earlier.observed_at)
       FROM price_history AS earlier
       WHERE earlier.source = listing.source
         AND earlier.listing_id = listing.listing_id
         AND earlier.observed_at <= listing.ends_at
   )
WHERE listing.ends_at IS NOT NULL
  AND listing.ends_at <= ?
ORDER BY listing.ends_at DESC
"""


@dataclass(frozen=True)
class ClosingPriceStore:
    """The closing-price view of the observation database."""

    database: Database

    def closed_by(self, moment: datetime) -> tuple[ClosingPrice, ...]:
        """Every lot whose close has passed, most recently closed first."""
        with self.database.connect() as connection:
            rows = connection.execute(
                _SELECT_CLOSED, (moment.astimezone(UTC).isoformat(),)
            ).fetchall()
        return tuple(_closing_price(row) for row in rows)


def _closing_price(row: tuple) -> ClosingPrice:
    """Rebuild one record from the row the query returned."""
    (
        source,
        listing_id,
        title,
        url,
        estimated_retail,
        ends_at,
        current_bid,
        bid_count,
        observed_at,
    ) = row
    return ClosingPrice(
        source=source,
        listing_id=listing_id,
        title=title,
        url=url,
        last_bid=Decimal(current_bid),
        ends_at=datetime.fromisoformat(ends_at),
        last_seen_at=datetime.fromisoformat(observed_at),
        bid_count=bid_count,
        estimated_retail=None if estimated_retail is None else Decimal(estimated_retail),
    )
