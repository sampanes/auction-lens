"""The watchlist as a person reads it, rather than as the file stores it.

The run report answers "what turned up today". This answers a different
question -- "what am I following, and where has it got to" -- so it is ordered
by what the person decided about a lot, not by what the scoring thought of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

from ..grading import HIGHEST_RATING, ConditionTag, Tag
from ..models import Verdict, WatchedItem

SEPARATOR = " | "

FILLED_STAR = "*"
EMPTY_STAR = "."
UNRATED_STAR = "-"

# What is being chased is read first. This is the order Verdict declares its
# members in; there is no second list to keep in step.
VERDICT_ORDER = tuple(Verdict)

# Worst news first within one lot, so a red tag is never printed below an amber.
CONCERN_ORDER = (Tag.RED, Tag.AMBER)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"

ALL_CLEAR = "every tag green"


def render_watchlist(items: tuple[WatchedItem, ...], *, path: str = "") -> str:
    """Render every followed lot, keenest first."""
    if not items:
        return f"Watchlist is empty{_at(path)}.\n"
    lines = [f"Following {len(items)} lot(s){_at(path)}."]
    for item in sorted(items, key=_keenness):
        lines.extend(_item_lines(item))
    return "\n".join(lines).rstrip() + "\n"


def _at(path: str) -> str:
    """Name the file, so a person knows which watchlist they are looking at."""
    return f" at {path}" if path else ""


def _keenness(item: WatchedItem) -> tuple:
    """Chased lots first, then the ones the provider rates highest, then by name."""
    return (
        VERDICT_ORDER.index(item.verdict),
        -(item.quality_rating or 0),
        item.title.lower(),
    )


def _item_lines(item: WatchedItem) -> Iterator[str]:
    yield ""
    yield f"[{item.verdict.upper()}] {stars_of(item.quality_rating)}  {item.title}"
    yield f"  {item.uid}"
    yield from _condition_lines(item.conditions)
    yield from _indented(_value_facts(item))
    yield from _indented(_price_facts(item))
    if item.note:
        yield f"  Note: {item.note}"
    if item.url:
        yield f"  {item.url}"
    if item.condition_photo_url:
        yield f"  Photo of this lot: {item.condition_photo_url}"


def stars_of(rating: int | None) -> str:
    """Five characters wide always, so a column of them lines up.

    A provider that does not rate its lots gets an empty scale rather than a
    zero-star one, because "unrated" and "rated worst" are not the same news.
    """
    if rating is None:
        return UNRATED_STAR * HIGHEST_RATING
    return FILLED_STAR * rating + EMPTY_STAR * (HIGHEST_RATING - rating)


def _condition_lines(conditions: tuple[ConditionTag, ...]) -> Iterator[str]:
    """One line per colour, worst first, so a rough lot is obvious at a glance.

    Amber means the provider was asked and did not answer. It gets its own line
    rather than being folded in with the red, because "nobody checked" is a
    different thing from "we checked and it is bad" -- and on the provider's own
    page it is shown as nothing at all.
    """
    if not conditions:
        return
    concerns = [tag for tag in conditions if tag.is_concerning]
    if not concerns:
        yield f"  Condition: {ALL_CLEAR}"
        return
    for colour in CONCERN_ORDER:
        labels = [tag.label for tag in concerns if tag.tag == colour]
        if labels:
            yield f"  [{colour.upper()}] {SEPARATOR.join(labels)}"


def _value_facts(item: WatchedItem) -> list[str]:
    """What the lot is said to be worth, and what the person thinks it is worth."""
    facts = []
    if item.estimated_retail is not None:
        facts.append(f"Retail {_money(item.estimated_retail)}")
    if item.my_estimate is not None:
        facts.append(f"My estimate {_money(item.my_estimate)}")
    if item.headroom is not None:
        facts.append(f"Headroom {_money(item.headroom)}")
    return facts


def _price_facts(item: WatchedItem) -> list[str]:
    """Where the price stands now, and how far it has travelled to get there."""
    latest = item.latest
    if latest is None:
        return ["Not seen in a run yet"]
    facts = [
        f"Bid {_money(latest.current_bid)}",
        f"Total {_money(latest.total_cost)}",
        f"{latest.bid_count} bid(s)",
    ]
    if item.movement is not None:
        first = item.first.scanned_at.strftime(TIMESTAMP_FORMAT)
        facts.append(f"{_signed(item.movement)} over {len(item.readings)} looks since {first}")
    facts.append(f"seen {latest.scanned_at.strftime(TIMESTAMP_FORMAT)}")
    return facts


def _indented(facts: list[str]) -> Iterator[str]:
    if facts:
        yield "  " + SEPARATOR.join(facts)


def _money(amount: Decimal) -> str:
    """Show a loss as -$4.00 rather than $-4.00, which reads as a typo."""
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount)}"


def _signed(amount: Decimal) -> str:
    """A price that has not moved is worth saying out loud, so it gets a word."""
    if amount == 0:
        return "unmoved"
    return f"{'+' if amount > 0 else '-'}${abs(amount)}"
