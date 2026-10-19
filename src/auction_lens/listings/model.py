"""One auction lot as the provider described it, and what a lot is called.

This is the record everything else in the project is about, and the only one
built from outside input, so it is also the only one here that has to parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..values import (
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
from .conditions import Grade, read_grade

REQUIRED_LISTING_FIELDS = ("source", "listing_id", "title", "url", "current_bid")

# Changes only affect a qualified match's reading order. Keeping these bonuses
# with ObservationChange lets the listing model describe that signal without
# depending on the higher-level matching feature that consumes it.
NEW_LISTING_PRIORITY_BONUS = 3
PRICE_CHANGE_PRIORITY_BONUS = 2

# What separates a provider from one of its own ids, everywhere. There were
# once two of these, a colon for storage identity and a slash for the key a
# person types, and they were one character apart. A record printed with the
# wrong one still looked like a key, so the mistake survived being read.
KEY_SEPARATOR = "/"

# CSV has no booleans, so a hand-written or exported row has to say one in
# words. JSON states a real one and is taken as it stands.
TRUE_WORDS = frozenset({"true", "yes", "on", "1"})


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
    # A lot received rather than bought: nothing is bid, and the value declared
    # for it is income to whoever takes it. Two consequences follow, and both
    # belong to whoever asks rather than to this record: the cost is a tax rate
    # applied to that value, and there is no purchase price that could be
    # anomalously low, so a bargain claim about such a lot means nothing.
    value_is_taxable_income: bool = False
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
            value_is_taxable_income=_flag(data, "value_is_taxable_income"),
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


@dataclass(frozen=True)
class PendingLot:
    """One closed lot still owed a reading of its own page.

    Carries the address because that is what a settling pass needs and the
    lot's stored row is the only place it exists, and the scheduled close
    because a result page that no longer states one still has to be compared
    against something.
    """

    source: str
    listing_id: str
    url: str
    scheduled_close: str


@dataclass(frozen=True)
class Settlement:
    """What a lot actually sold for, as its own page stated after the close.

    Distinct from every other price in this program, which is a reading taken
    while bidding was still running and is therefore a floor. This is the sale.

    Both close times are kept because they are different facts. A bid placed
    near the end pushes the close out in fixed steps, so the moment a lot was
    scheduled to end and the moment it ended can differ, and ``extended`` says
    whether they did without a reader having to subtract them.
    """

    source: str
    listing_id: str
    final_bid: Decimal
    bid_count: int
    closed_at: datetime | None
    scheduled_close: datetime | None
    extended: bool

    @property
    def key(self) -> str:
        return key_of(self.source, self.listing_id)


@dataclass(frozen=True)
class SettleOutcome:
    """What one settling pass read, and what it could not.

    The failures are counted separately because they mean different things to
    whoever reads the run. A lot still open was asked too early and will answer
    next time; a missing one was withdrawn and never will; an unreadable one is
    a parsing problem worth knowing about rather than a fact about the lot; an
    unreachable one never answered at all, which is not about the lot in any
    way and may be about every lot still to come.
    """

    settlements: tuple[Settlement, ...] = ()
    still_open: int = 0
    missing: int = 0
    unreadable: int = 0
    unreachable: int = 0
    # Why the pass ended before it ran out of lots, in words a report can
    # print, and empty when it did not. A pass that stopped early is not a
    # failed one: what it read is still stored and the rest stays owed. The
    # reason is carried rather than raised precisely so that is possible --
    # an exception leaving here discards every reading the pass had gathered,
    # which is how this provider's nightly run stored five rows in a month.
    stopped_early: str = ""

    @property
    def requested(self) -> int:
        return (
            len(self.settlements)
            + self.still_open
            + self.missing
            + self.unreadable
            + self.unreachable
        )


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


def _flag(data: dict[str, Any], key: str) -> bool:
    """Read a boolean that may have arrived as JSON or as a word in a CSV cell."""
    value = data.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in TRUE_WORDS


def _text(data: dict[str, Any], key: str) -> str:
    return str(data.get(key) or "").strip()
