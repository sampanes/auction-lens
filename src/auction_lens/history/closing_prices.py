"""Closing-price floors, from their database query to their readable report.

Nothing new is written here. Every fact this needs was already being recorded:
``listings`` knows when a lot closed, and ``price_history`` knows what it cost
each time it was looked at. Putting those two together is the only step that
was missing, and it is a read, so the answer improves on its own as more looks
accumulate rather than needing a migration.

The useful reading is the last one taken *before* the close. A look
taken after a lot ended is not evidence about that auction -- the page may be
stale, or the lot may have been relisted -- so this asks the database for the
last look while the lot was still open, and reports how late that look was.

Every quoted price is therefore a floor rather than a final sale price. The
report keeps that limitation visible and counts readings that were taken too
early to be useful instead of silently dropping them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..listings.model import key_of
from ..values import require_not_negative
from .database import Database

DEFAULT_WITHIN_MINUTES = 30
CLOSE_FORMAT = "%a %d %b %H:%M"
FLOOR_NOTE = "Each price is a floor: the lot sold for at least this much."


@dataclass(frozen=True)
class ClosingPrice:
    """The last bid seen on a lot while it was still open.

    What a lot actually sold for is not knowable here. The provider never
    publishes a hammer price, and a closed lot drops off the pages this reads,
    so the final bid is always one look too late. What is knowable is a floor:
    the lot sold for *at least* this much.

    ``seen_minutes_before_close`` says how tight that floor is, and is the
    whole value of the record. A bid read three minutes before the close is
    nearly the sale price; the same bid read six hours before says almost
    nothing. Every reader has to be able to tell those apart, so the two facts
    travel together and neither is stored without the other.
    """

    source: str
    listing_id: str
    title: str
    url: str
    last_bid: Decimal
    ends_at: datetime
    last_seen_at: datetime
    bid_count: int = 0
    estimated_retail: Decimal | None = None

    def __post_init__(self) -> None:
        require_not_negative(self.last_bid, field_name="last_bid")
        require_not_negative(self.bid_count, field_name="bid_count")
        if self.last_seen_at > self.ends_at:
            raise ValueError("last_seen_at must not be later than ends_at")

    @property
    def key(self) -> str:
        """The key a person copies to say which lot they mean."""
        return key_of(self.source, self.listing_id)

    @property
    def seen_minutes_before_close(self) -> int:
        """How long before the close this bid was true, rounded down."""
        return int((self.ends_at - self.last_seen_at).total_seconds() // 60)

    @property
    def share_of_retail(self) -> Decimal | None:
        """The floor price against the provider's estimate, when there is one."""
        if not self.estimated_retail:
            return None
        return self.last_bid / self.estimated_retail

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


def render_closing_prices(
    prices: tuple[ClosingPrice, ...],
    zone: ZoneInfo,
    *,
    within_minutes: int = DEFAULT_WITHIN_MINUTES,
    limit: int | None = None,
) -> str:
    """Render reliable closing-price floors and account for omitted readings."""
    tight, stale = _split_by_tightness(prices, within_minutes)
    if not tight:
        return _nothing_tight_enough(prices, stale, within_minutes)

    shown = tight if limit is None else tight[:limit]
    lines = [
        f"{len(tight)} lot(s) were last looked at within {within_minutes} minute(s) "
        "of closing.",
        FLOOR_NOTE,
    ]
    for price in shown:
        lines.extend(_price_lines(price, zone))
    lines.extend(_omission_lines(len(tight) - len(shown), stale, within_minutes))
    return "\n".join(lines).rstrip() + "\n"


def _split_by_tightness(
    prices: tuple[ClosingPrice, ...], within_minutes: int
) -> tuple[list[ClosingPrice], list[ClosingPrice]]:
    """Separate readings worth quoting from readings taken too early."""
    tight = [
        price for price in prices if price.seen_minutes_before_close <= within_minutes
    ]
    stale = [
        price for price in prices if price.seen_minutes_before_close > within_minutes
    ]
    tight.sort(key=lambda price: price.seen_minutes_before_close)
    return tight, stale


def _nothing_tight_enough(
    prices: tuple[ClosingPrice, ...],
    stale: list[ClosingPrice],
    within_minutes: int,
) -> str:
    """Explain an empty answer, which here is a schedule problem, not a bug."""
    if not prices:
        return "No lot in the database has closed yet.\n"
    return (
        f"No lot was looked at within {within_minutes} minute(s) of closing, so "
        f"there is no price worth quoting.\n{len(stale)} closed lot(s) were seen "
        "only earlier than that. Run the collector nearer the hour lots close "
        "and this fills in on its own.\n"
    )


def _price_lines(price: ClosingPrice, zone: ZoneInfo) -> Iterator[str]:
    """One lot: what it reached, how tight the reading is, and which lot it was."""
    yield ""
    yield (
        f"  at least {_money(price.last_bid)}{_against_retail(price)}, "
        f"{price.bid_count} bid(s), "
        f"seen {price.seen_minutes_before_close}m before it closed"
    )
    yield f"    closed {price.ends_at.astimezone(zone).strftime(CLOSE_FORMAT)}"
    yield f"    {price.key}  {price.title}"


def _against_retail(price: ClosingPrice) -> str:
    """Compare with the provider's estimate only when there is one to compare."""
    share = price.share_of_retail
    if share is None:
        return ""
    return (
        f" of {_money(price.estimated_retail)} estimated retail "
        f"({share * 100:.0f}%)"
    )


def _omission_lines(
    trimmed: int, stale: list[ClosingPrice], within_minutes: int
) -> Iterator[str]:
    """Say what is not shown, so a short list is never mistaken for all."""
    if trimmed:
        yield ""
        yield f"{trimmed} further tight reading(s) not shown; raise the limit to see them."
    if stale:
        yield ""
        yield (
            f"{len(stale)} closed lot(s) left out: last looked at more than "
            f"{within_minutes} minute(s) before closing, which says little about "
            "what they sold for."
        )


def _money(amount: Decimal) -> str:
    """Whole dollars; cents are noise at the resolution this answers."""
    return f"${amount:,.0f}"
