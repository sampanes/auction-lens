"""The watchlist as a person reads it, rather than as the file stores it.

The run report answers "what turned up today". This answers a different
question -- "what am I following, and where has it got to" -- so it is ordered
by what the person decided about a lot, not by what the scoring thought of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from html import escape

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

# Terminal colours, which are plain ASCII escape sequences. They are written
# only when the caller says the output is a terminal, so a redirected or piped
# watchlist stays clean, and the colour word is printed either way -- colour is
# how the line is skimmed, never the only place the news is.
COLOURS = {Tag.RED: "31", Tag.AMBER: "33", Tag.GREEN: "32"}
ESCAPE = "\x1b"
PLAIN = f"{ESCAPE}[0m"

BODY_STYLE = "font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#202124"
CARD_STYLE = (
    "border:1px solid #dadce0;border-radius:12px;padding:20px;margin:16px 0;"
    "background:#ffffff"
)
PHOTO_STYLE = (
    "display:block;width:100%;max-height:360px;object-fit:cover;border-radius:8px;"
    "margin:16px 0"
)
FACT_STYLE = (
    "display:inline-block;background:#f1f3f4;border-radius:6px;padding:7px 10px;"
    "margin:0 6px 7px 0;font-size:14px"
)
BUTTON_STYLE = (
    "display:inline-block;background:#1967d2;color:#ffffff;text-decoration:none;"
    "font-weight:bold;border-radius:6px;padding:10px 16px"
)
TAG_STYLES = {
    Tag.RED: "background:#fce8e6;color:#a50e0e",
    Tag.AMBER: "background:#fef7e0;color:#7c4a03",
}


def render_watchlist(
    items: tuple[WatchedItem, ...], *, path: str = "", colour: bool = False
) -> str:
    """Render every followed lot, keenest first."""
    if not items:
        return f"Watchlist is empty{_at(path)}.\n"
    lines = [f"Following {len(items)} lot(s){_at(path)}."]
    for item in sorted(items, key=_keenness):
        lines.extend(_item_lines(item, colour=colour))
    return "\n".join(lines).rstrip() + "\n"


def render_watchlist_html(items: tuple[WatchedItem, ...], *, path: str = "") -> str:
    """Render the same watchlist as phone-friendly email cards."""
    if not items:
        return f"<p>Watchlist is empty{escape(_at(path))}.</p>"
    cards = "".join(_html_card(item) for item in sorted(items, key=_keenness))
    headline = f"Following {len(items)} lot(s){_at(path)}."
    return "".join(
        (
            f"<div style='{BODY_STYLE}'>",
            "<p style='color:#5f6368;font-size:12px;font-weight:bold;"
            "letter-spacing:1px'>AUCTION LENS</p>",
            f"<h2 style='margin:0 0 8px'>{escape(headline)}</h2>",
            "<p style='color:#5f6368;margin-top:0'>The lots you marked for a closer look.</p>",
            cards,
            "</div>",
        )
    )


def _html_card(item: WatchedItem) -> str:
    concerns = _html_conditions(item.conditions)
    facts = (*_value_facts(item), *_price_facts(item))
    details = "".join(f"<span style='{FACT_STYLE}'>{escape(fact)}</span>" for fact in facts)
    note = f"<p><strong>Note:</strong> {escape(item.note)}</p>" if item.note else ""
    link = (
        f"<p style='margin-bottom:0'><a href='{escape(item.url, quote=True)}' "
        f"style='{BUTTON_STYLE}'>View listing</a></p>"
        if item.url
        else ""
    )
    photo = (
        f"<a href='{escape(item.condition_photo_url, quote=True)}'>"
        f"<img src='{escape(item.condition_photo_url, quote=True)}' "
        f"alt='Photo of this lot' style='{PHOTO_STYLE}'></a>"
        if item.condition_photo_url
        else ""
    )
    return "".join(
        (
            f"<article style='{CARD_STYLE}'>",
            f"<h3 style='margin:0 0 8px'>{escape(item.title or item.uid)}</h3>",
            f"<p style='margin:0 0 14px;color:#1967d2;font-weight:bold'>"
            f"{escape(str(item.verdict).upper())} &nbsp; "
            f"{escape(stars_of(item.quality_rating))}</p>",
            concerns,
            photo,
            f"<div>{details}</div>" if details else "",
            note,
            link,
            "</article>",
        )
    )


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


def _item_lines(item: WatchedItem, *, colour: bool) -> Iterator[str]:
    yield ""
    yield f"[{item.verdict.upper()}] {stars_of(item.quality_rating)}  {item.title}"
    yield f"  {item.uid}{_relisting(item)}"
    yield from _condition_lines(item.conditions, colour=colour)
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


def _relisting(item: WatchedItem) -> str:
    """Say when a trail spans more than the auction the lot is in today.

    An item that did not sell comes back under a new auction id. Following the
    item rather than the auction is what lets the trail say so.
    """
    auctions = item.auctions_seen
    return f"  (seen in {auctions} auctions)" if auctions > 1 else ""


def _paint(body: str, tag: Tag, *, colour: bool) -> str:
    if not colour:
        return body
    return f"{ESCAPE}[{COLOURS[tag]}m{body}{PLAIN}"


def _condition_lines(
    conditions: tuple[ConditionTag, ...], *, colour: bool
) -> Iterator[str]:
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
    for shade, labels in _concern_groups(concerns):
        if labels:
            yield "  " + _paint(
                f"[{shade.upper()}] {SEPARATOR.join(labels)}", shade, colour=colour
            )


def _html_conditions(conditions: tuple[ConditionTag, ...]) -> str:
    if not conditions:
        return ""
    concerns = tuple(tag for tag in conditions if tag.is_concerning)
    if not concerns:
        return f"<p>Condition: {ALL_CLEAR}</p>"
    badges = []
    for shade, labels in _concern_groups(concerns):
        badges.append(
            f"<span style='display:inline-block;{TAG_STYLES[shade]};border-radius:999px;"
            f"padding:5px 9px;margin:0 6px 7px 0;font-size:13px;font-weight:bold'>"
            f"{escape(str(shade).upper())}: {escape(', '.join(labels))}</span>"
        )
    return "<div>" + "".join(badges) + "</div>"


def _concern_groups(
    concerns: tuple[ConditionTag, ...] | list[ConditionTag],
) -> Iterator[tuple[Tag, list[str]]]:
    """Group condition labels once, in the order every rendering uses."""
    for shade in CONCERN_ORDER:
        labels = [tag.label for tag in concerns if tag.tag == shade]
        if labels:
            yield shade, labels


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
