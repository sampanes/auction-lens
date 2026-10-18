"""What a lot actually sold for, read from its own page once it had closed.

Every other price this project stores is a reading taken while an auction was
still running, which makes it a floor rather than a sale. That was believed to
be the best available answer, because a closed lot drops out of the search
pages discovery reads -- measured, not one closed row appeared in 2,580 cached
search rows, and lots scheduled to close at 21:07 were last seen at 20:00.

A lot's own page is different: it stays up after the close and states the
result. That is the only place a hammer price exists, so it is the only place
this module gets one.

The distinction is worth the table. Across one night's 2,810 closing lots, 70%
were last read more than thirty minutes before even their scheduled close, and
the error on those cannot be measured from readings alone, because the lots
with no late reading are exactly the ones whose late movement went unseen. A
settled price ends that argument instead of narrowing it.

Both close times are kept. A bid placed near the end pushes the close out by
``extensionInterval`` seconds, so the moment a lot was scheduled to end and the
moment it ended are different facts, and the second one was never recorded
before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..listings.model import PendingLot, Settlement
from .database import Database

# A nightly run only needs last night. The bound exists so a first run cannot
# walk the whole recorded history at a request each, which is tens of thousands.
DEFAULT_SETTLE_SINCE_HOURS = 24

_UPSERT_SETTLEMENT = """
INSERT INTO settled_closes (
    source, listing_id, final_bid, bid_count,
    closed_at, scheduled_close, extended, settled_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source, listing_id) DO UPDATE SET
    final_bid=excluded.final_bid,
    bid_count=excluded.bid_count,
    closed_at=excluded.closed_at,
    scheduled_close=excluded.scheduled_close,
    extended=excluded.extended,
    settled_at=excluded.settled_at
"""

# Lots whose scheduled close has passed, that carry an address to read, and
# that nothing has settled yet. Oldest close first, so a run cut short by a
# limit or an interruption makes progress through the backlog rather than
# re-reading the same newest lots every night.
_UNSETTLED = """
SELECT l.source, l.listing_id, l.url, l.ends_at
FROM listings AS l
LEFT JOIN settled_closes AS s
       ON s.source = l.source AND s.listing_id = l.listing_id
WHERE l.ends_at IS NOT NULL
  AND l.ends_at <= ?
  AND l.ends_at >= ?
  AND l.url <> ''
  AND s.listing_id IS NULL
ORDER BY l.ends_at
"""

_COUNT_UNSETTLED = f"SELECT COUNT(*) FROM ({_UNSETTLED})"


@dataclass(frozen=True)
class SettlementStore:
    """Settled closes, which are written only by a pass that read a shut lot."""

    database: Database

    def record(self, settlements: list[Settlement], *, settled_at: datetime) -> int:
        """Store what these lots sold for, replacing any earlier answer.

        Replacing rather than ignoring, because a lot read while its page was
        still settling can be read again later and the second answer is the
        better one. The count returned is of rows offered, not rows changed:
        a caller reports what it read, and SQLite's change count would also
        swallow a re-read that happened to agree.
        """
        if not settlements:
            return 0
        stamp = settled_at.isoformat()
        with self.database.connect() as connection:
            connection.executemany(
                _UPSERT_SETTLEMENT,
                [
                    (
                        item.source,
                        item.listing_id,
                        str(item.final_bid),
                        item.bid_count,
                        None if item.closed_at is None else item.closed_at.isoformat(),
                        None
                        if item.scheduled_close is None
                        else item.scheduled_close.isoformat(),
                        int(item.extended),
                        stamp,
                    )
                    for item in settlements
                ],
            )
        return len(settlements)

    def pending(
        self, *, closed_before: datetime, closed_after: datetime, limit: int | None = None
    ) -> tuple[PendingLot, ...]:
        """Closed lots still owed a reading, oldest close first.

        Bounded at both ends on purpose. The near end keeps a run from reading a
        lot that may still be extending; the far end keeps a first run from
        walking the whole recorded history, which is tens of thousands of lots
        and a request each.
        """
        query = _UNSETTLED if limit is None else f"{_UNSETTLED} LIMIT {int(limit)}"
        with self.database.connect() as connection:
            rows = connection.execute(
                query, (closed_before.isoformat(), closed_after.isoformat())
            ).fetchall()
        return tuple(
            PendingLot(
                source=str(row[0]),
                listing_id=str(row[1]),
                url=str(row[2]),
                scheduled_close=str(row[3]),
            )
            for row in rows
        )

    def pending_count(
        self, *, closed_before: datetime, closed_after: datetime
    ) -> int:
        """How many closed lots are still owed a reading, for a run to report."""
        with self.database.connect() as connection:
            row = connection.execute(
                _COUNT_UNSETTLED, (closed_before.isoformat(), closed_after.isoformat())
            ).fetchone()
        return int(row[0]) if row else 0
