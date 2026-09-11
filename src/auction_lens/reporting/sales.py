"""Closing prices as a person reads them.

The run report answers "what is worth bidding on tonight". This answers the
question that only history can answer -- "what does this kind of thing actually
go for" -- and every line of it is a floor rather than a sale price, so the
rendering says so once at the top and then never lets a reader forget which
readings are tight enough to trust.

Readings are ordered by how close to the close they were taken, because that is
the quality of the evidence. A stale reading is reported as a count rather than
dropped in silence: knowing that four hundred lots were looked at too early is
itself the answer to "why is this list so short".
"""

from __future__ import annotations

from collections.abc import Iterator
from zoneinfo import ZoneInfo

from ..models import ClosingPrice

# A reading taken within this long of the close is tight enough that the floor
# is worth quoting. It is a default rather than a rule; the caller may widen it.
DEFAULT_WITHIN_MINUTES = 30

CLOSE_FORMAT = "%a %d %b %H:%M"

FLOOR_NOTE = "Each price is a floor: the lot sold for at least this much."


def render_closing_prices(
    prices: tuple[ClosingPrice, ...],
    zone: ZoneInfo,
    *,
    within_minutes: int = DEFAULT_WITHIN_MINUTES,
    limit: int | None = None,
) -> str:
    """Render the tight readings, and account for the ones left out."""
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
    """Say what is not on the screen, so a short list is never mistaken for all."""
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


def _money(amount) -> str:
    """Whole dollars; cents are noise at the resolution this answers."""
    return f"${amount:,.0f}"
