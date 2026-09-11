"""One auction lot as the provider described it, and what a lot is called.

This is the record everything else in the project is about, and the only one
built from outside input, so it is also the only one here that has to parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..fields import (
    is_absent,
    parse_dimensions,
    parse_labels,
    parse_money,
    parse_optional_decimal,
    parse_optional_money,
    parse_optional_rate,
    parse_urls,
    parse_utc_datetime,
    parse_whole_number,
)
from ..grading import Grade, read_grade
from .scale import NEW_LISTING_PRIORITY_BONUS, PRICE_CHANGE_PRIORITY_BONUS

REQUIRED_LISTING_FIELDS = ("source", "listing_id", "title", "url", "current_bid")

# What separates a provider from one of its own ids, everywhere. There were
# once two of these, a colon for storage identity and a slash for the key a
# person types, and they were one character apart. A record printed with the
# wrong one still looked like a key, so the mistake survived being read.
KEY_SEPARATOR = "/"


def key_of(source: str, identifier: str) -> str:
    """Name one lot: a provider, and one of its own ids.

    An id alone is only unique per site, so the provider always travels with
    it. Which id is the caller's choice, and the two that matter have their own
    named properties: ``key`` is the auction open today, which is what a person
    reads and types back, and ``item_key`` is the physical item, which survives
    a relisting. Both are spelled the same way on purpose -- a person pasting
    either one should not have to know which they are holding.
    """
    return f"{source}{KEY_SEPARATOR}{identifier}"


@dataclass(frozen=True)
class Listing:
    """One auction lot as the provider described it at one moment in time."""

    source: str
    listing_id: str
    title: str
    url: str
    current_bid: Decimal
    estimated_retail: Decimal | None = None
    bid_count: int = 0
    ends_at: datetime | None = None
    location: str = ""
    inventory_id: str = ""
    # What the warehouse wrote on the lot after looking at it: "blower not
    # included", "MISSING POWER SOURCE", "leaks air/ needs a patch". Rarer
    # than a title and worth far more, because a title is the manufacturer's
    # words and this is the only sentence about this particular item.
    notes: str = ""
    conditions: tuple[str, ...] = ()
    photo_urls: tuple[str, ...] = ()
    grade: Grade | None = None
    buyer_premium_rate: Decimal | None = None
    brand: str = ""
    model: str = ""
    category: str = ""
    handling_weight_lb: Decimal | None = None
    package_dimensions_in: tuple[Decimal, ...] = ()
    loading_assistance: tuple[str, ...] = ()
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def lot_key(self) -> str:
        """What names the *thing*, rather than the auction it is currently in.

        A lot that does not sell is relisted under a new auction id, so the
        auction id alone loses the fact that this exact item has been round
        before. The physical item id survives that; a provider that does not
        give one falls back to the auction id, which is all it has.
        """
        return self.inventory_id or self.listing_id

    @property
    def key(self) -> str:
        """The name of this auction, which is what every report prints.

        Four places used to build this string themselves, each writing the
        separator out by hand. A key a person types is exactly the sort of
        thing that must have one spelling.
        """
        return key_of(self.source, self.listing_id)

    @property
    def item_key(self) -> str:
        """The name of the thing, which is what survives its relisting."""
        return key_of(self.source, self.lot_key)

    @property
    def searchable_text(self) -> str:
        """Everything a person reads in the headline, lowercased for matching.

        One authority, because interests and logistics both ask questions of the
        same words: if a provider states a fact anywhere here, both should see
        it, and neither should carry its own idea of where to look.
        """
        return " ".join((self.title, self.location, *self.conditions)).lower()

    @property
    def disqualifying_text(self) -> str:
        """Everything that can rule a lot out, which is more than what names it.

        The warehouse note is the only sentence written about this particular
        item rather than about the model -- "blower not included", "MISSING
        POWER SOURCE", "leaks air/ needs a patch" -- so it has to be able to
        take a lot out of the report.

        It deliberately cannot put one in. A pallet lot's note lists everything
        on the pallet, so reading wants from here would make one pallet match
        every interest at once. A note is evidence against, never for.

        Whitespace is flattened here rather than where the note was read, so
        the guarantee holds whichever file it arrived in: a person typing into
        a box over several visits must not defeat a match by where they
        happened to press Enter.
        """
        return f"{self.searchable_text} {' '.join(self.notes.split())}".lower()

    @property
    def stock_photo_url(self) -> str:
        """The manufacturer's photo, which shows the model rather than the lot."""
        return self.photo_urls[0] if self.photo_urls else ""

    @property
    def condition_photo_url(self) -> str:
        """The last photo, which is the one taken of this actual lot."""
        return self.photo_urls[-1] if self.photo_urls else ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Listing:
        """Build a listing from one canonical JSON object or CSV row.

        The field list is deliberately spelled out rather than driven by a
        table: this is the shape of the file an operator hands us, and it
        should be readable as such. See docs/CONVENTIONS.md.
        """
        missing = [key for key in REQUIRED_LISTING_FIELDS if is_absent(data.get(key))]
        if missing:
            raise ValueError(f"missing required listing fields: {', '.join(missing)}")
        grade = read_grade(data.get("grade"), data.get("quality_rating"))
        return cls(
            source=_text(data, "source"),
            listing_id=_text(data, "listing_id"),
            title=_text(data, "title"),
            url=_text(data, "url"),
            current_bid=parse_money(data["current_bid"], field_name="current_bid"),
            estimated_retail=parse_optional_money(
                data.get("estimated_retail"), field_name="estimated_retail"
            ),
            bid_count=parse_whole_number(data.get("bid_count"), field_name="bid_count"),
            ends_at=parse_utc_datetime(data.get("ends_at"), field_name="ends_at"),
            location=_text(data, "location"),
            inventory_id=_text(data, "inventory_id"),
            notes=_text(data, "notes"),
            conditions=_condition_words(data, grade),
            photo_urls=_photos(data),
            grade=grade,
            buyer_premium_rate=parse_optional_rate(
                data.get("buyer_premium_rate"), field_name="buyer_premium_rate"
            ),
            brand=_text(data, "brand"),
            model=_text(data, "model"),
            category=_text(data, "category").lower(),
            handling_weight_lb=parse_optional_decimal(
                data.get("handling_weight_lb"), field_name="handling_weight_lb"
            ),
            package_dimensions_in=parse_dimensions(data.get("package_dimensions_in")),
            loading_assistance=parse_labels(data.get("loading_assistance")),
            observed_at=parse_utc_datetime(data.get("observed_at"), field_name="observed_at")
            or datetime.now(UTC),
        )


@dataclass(frozen=True)
class ObservationChange:
    """What the stored history says about a listing seen again."""

    is_new: bool
    price_changed: bool
    previous_bid: Decimal | None = None

    @property
    def priority_bonus(self) -> int:
        """A small reading-order bias that cannot make a lot reportable."""
        if self.is_new:
            return NEW_LISTING_PRIORITY_BONUS
        if self.price_changed:
            return PRICE_CHANGE_PRIORITY_BONUS
        return 0


def _photos(data: dict[str, Any]) -> tuple[str, ...]:
    """Read a gallery, accepting the single image older files carry."""
    return parse_urls(data.get("photo_urls")) or parse_urls(data.get("image_url"))


def _condition_words(data: dict[str, Any], grade: Grade | None) -> tuple[str, ...]:
    """Let a grade speak for the condition when the provider gave one.

    A provider that grades its lots would otherwise say the same thing twice --
    once as tags and once as loose words -- and the two would drift. Scoring
    keeps matching the words it always matched either way.
    """
    if grade is not None:
        return grade.words
    return parse_labels(data.get("conditions"))


def _text(data: dict[str, Any], key: str) -> str:
    return str(data.get(key) or "").strip()
