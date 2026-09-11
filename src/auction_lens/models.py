"""The domain model every other module speaks.

These records are immutable and free of behavior on purpose: acquisition,
scoring, valuation, storage, and reporting all pass them around, so the model
stays the one thing in the project with no dependencies of its own.

A record that is built from outside input checks itself in ``__post_init__``,
so an invalid one cannot exist for any caller to trip over. Records that are
*derived* from already-checked ones do not re-check; see docs/CONVENTIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .fields import (
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
    require_at_least,
    require_finite,
    require_not_negative,
)
from .grading import ConditionTag, Grade, read_grade

REQUIRED_LISTING_FIELDS = ("source", "listing_id", "title", "url", "current_bid")

# What separates a provider from its own listing id in a lot's unique name.
UID_SEPARATOR = ":"

# What separates them in the key a person copies out of a report and types
# back at a command. Different on purpose: see ``listing_key_of``.
LISTING_KEY_SEPARATOR = "/"

# The scale every score lives on. Scoring clamps to it and configuration is
# checked against it, so both read it from the record they are talking about.
LOWEST_SCORE = 0
HIGHEST_SCORE = 100

# A fresh auction event deserves to be read before an equally good old one,
# and a moved price deserves nearly the same attention. These affect reading
# order only: observation history is allowed to reorder a report, but never to
# decide whether a listing clears a configured quality bar.
NEW_LISTING_PRIORITY_BONUS = 3
PRICE_CHANGE_PRIORITY_BONUS = 2


class LogisticsStatus(StrEnum):
    """How settled the question of getting one item home is."""

    ORDINARY = "ordinary"
    NEEDS_PLAN = "needs_plan"
    ASSUMED_FEASIBLE = "assumed_feasible"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"


# The two an operator may record. The rest are conclusions Auction Lens drew,
# and a person overrides them by answering rather than by restating them.
OPERATOR_DECIDABLE = (LogisticsStatus.FEASIBLE, LogisticsStatus.INFEASIBLE)


class CandidateCategory(StrEnum):
    """Why a listing is being reported at all."""

    WANTED = "wanted"
    ANOMALY = "anomaly"


class Verdict(StrEnum):
    """What a person has decided about a lot they are following.

    This is the person's own word, and is nothing to do with the provider's
    condition tags. Those live in ``grading`` and are the lot's, not theirs.
    """

    # Declared in the order a person reads them: what is being chased first,
    # what was decided against last. Sorting reads this order and nothing else.
    HUNTING = "hunting"
    WATCHING = "watching"
    WON = "won"
    LOST = "lost"
    PASSED = "passed"


@dataclass(frozen=True)
class InterestRef:
    """A stable interest identity with the name a person saw at the time.

    The id answers which configured want this was. The name is kept beside it
    because an old decision should remain readable after that want is renamed.
    """

    interest_id: str
    name: str

    def __post_init__(self) -> None:
        interest_id = self.interest_id.strip()
        name = self.name.strip()
        if not interest_id:
            raise ValueError("interest id must be non-empty text")
        if not name:
            raise ValueError("interest name must be non-empty text")
        object.__setattr__(self, "interest_id", interest_id)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True)
class InterestProgress:
    """How a configured want stands against its explicit fulfillments."""

    interest: InterestRef
    wanted: int | None
    fulfilled: int = 0

    def __post_init__(self) -> None:
        if self.wanted is not None:
            require_at_least(self.wanted, 1, field_name="wanted")
        require_not_negative(self.fulfilled, field_name="fulfilled")

    @property
    def is_limited(self) -> bool:
        return self.wanted is not None

    @property
    def is_retired(self) -> bool:
        return self.wanted is not None and self.fulfilled >= self.wanted

    @property
    def remaining(self) -> int | None:
        if self.wanted is None:
            return None
        return max(self.wanted - self.fulfilled, 0)


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


@dataclass(frozen=True)
class LogisticsDecision:
    """An operator's saved answer to a handling question for one listing."""

    status: LogisticsStatus
    added_cost: Decimal = Decimal("0")
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _decidable(self.status))
        require_not_negative(self.added_cost, field_name="added_cost")


@dataclass(frozen=True)
class LogisticsAssessment:
    """What handling stages are still unresolved for one listing."""

    status: LogisticsStatus
    questions: tuple[str, ...] = ()
    added_cost: Decimal = Decimal("0")
    decision_note: str = ""


@dataclass(frozen=True)
class ValuationObservation:
    """A source's price claim; retaining provenance keeps aggregation auditable."""

    source_id: str
    basis: str
    low: Decimal
    typical: Decimal
    high: Decimal
    currency: str = "USD"
    sample_size: int = 1
    confidence: Decimal = Decimal("1")
    observed_at: datetime | None = None
    url: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        for field_name in ("low", "typical", "high"):
            require_finite(getattr(self, field_name), field_name=field_name)
        if not self.low <= self.typical <= self.high:
            raise ValueError("valuation requires low <= typical <= high")
        require_at_least(self.sample_size, 1, field_name="sample_size")
        require_not_negative(self.confidence, field_name="confidence")


@dataclass(frozen=True)
class ValuationBand:
    """Several observations of one basis, combined into a single range."""

    basis: str
    low: Decimal
    typical: Decimal
    high: Decimal
    source_count: int
    sample_size: int


@dataclass(frozen=True)
class ResearchLink:
    """A place for a person to check value; nothing is fetched from it."""

    source_id: str
    label: str
    url: str


@dataclass(frozen=True)
class ValuationSummary:
    """Everything the valuation fan-out learned about one listing."""

    bands: tuple[ValuationBand, ...] = ()
    observations: tuple[ValuationObservation, ...] = ()
    research_links: tuple[ResearchLink, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    """One listing that matched one rule, with the evidence for reporting it."""

    listing: Listing
    category: CandidateCategory
    rule_id: str
    rule_name: str
    score: int
    total_cost: Decimal
    retail_ratio: Decimal | None
    reasons: tuple[str, ...]
    change: ObservationChange
    valuation: ValuationSummary | None = None
    logistics: LogisticsAssessment | None = None
    weight: Decimal = Decimal("1")

    @property
    def priority(self) -> Decimal:
        """Reading order: quality plus fresh news, scaled by how much it was wanted.

        Deliberately separate from ``score``. Score answers "is this worth
        reporting at all", and every configured bar is tuned against it.
        Priority answers "what should be read first". A new listing or changed
        price can move an already-qualified candidate up, but cannot push it
        past a bar. The clamp preserves the same ceiling as every score.
        """
        attention_score = min(HIGHEST_SCORE, self.score + self.change.priority_bonus)
        return attention_score * self.weight


class ReadingOrder(StrEnum):
    """What "first" means in a report.

    Priority is the default and the one every bar is tuned against: how good a
    lot is, scaled by how much this operator wanted it.

    Retail answers a different question -- what is the most valuable thing here
    -- which is the one somebody asks when they are about to go and collect,
    and it deliberately ignores how well the lot scored.
    """

    PRIORITY = "priority"
    RETAIL = "retail"


def ranked(
    candidates: list[Candidate],
    limit: int | None = None,
    order: ReadingOrder = ReadingOrder.PRIORITY,
) -> list[Candidate]:
    """Best first, and optionally only the best few.

    One authority for reading order, because a cap means "the best" only if
    whatever applies it agrees with whatever renders it about which those are.

    The limit takes the top of the existing ranking rather than introducing a
    bar of its own: the weights decide what is worth reading, and this only
    decides how long a report a person will actually finish.
    """
    best = sorted(candidates, key=_reading_key(order), reverse=True)
    return best if limit is None else best[:limit]


def _reading_key(order: ReadingOrder):
    """The one value each ordering sorts on.

    A lot with no stated retail sorts last rather than first, because an
    unknown value is not a large one. Priority breaks ties either way, so two
    lots of the same worth still arrive in a sensible order.
    """
    if order == ReadingOrder.RETAIL:
        return lambda item: (item.listing.estimated_retail or Decimal(0), item.priority)
    return lambda item: (item.priority,)


@dataclass(frozen=True)
class PriceReading:
    """One look at a lot: what it cost at that moment.

    A scan every hour leaves twenty-four of these in a day; a scan once leaves
    one. That is the whole point of keeping them as a list rather than as a
    single "current price" that forgets everything it replaces.

    The auction is recorded on the reading rather than on the item, because a
    trail that spans a relisting has readings from more than one auction.
    """

    scanned_at: datetime
    current_bid: Decimal
    total_cost: Decimal
    bid_count: int = 0
    listing_id: str = ""

    def __post_init__(self) -> None:
        require_not_negative(self.current_bid, field_name="current_bid")
        require_not_negative(self.total_cost, field_name="total_cost")
        require_not_negative(self.bid_count, field_name="bid_count")


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
        return listing_key_of(self.source, self.listing_id)

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


@dataclass(frozen=True)
class WatchedItem:
    """One lot a person is following, and every look they have taken at it.

    The first block is what the provider said, refreshed on every run. The
    second is automatic match provenance. The third is what the person thinks,
    which no run may overwrite.
    """

    source: str
    listing_id: str
    inventory_id: str = ""
    title: str = ""
    url: str = ""
    photo_urls: tuple[str, ...] = ()
    estimated_retail: Decimal | None = None
    conditions: tuple[ConditionTag, ...] = ()
    quality_rating: int | None = None
    # What the scorer said when this lot entered the watchlist. These are
    # automatic provenance and may be refreshed as current rules are renamed.
    matched_interests: tuple[InterestRef, ...] = ()

    my_estimate: Decimal | None = None
    verdict: Verdict = Verdict.WATCHING
    note: str = ""
    # Which wants the person says this purchase actually satisfied. Winning a
    # multi-match lot never fills every want by accident.
    fulfilled_interests: tuple[InterestRef, ...] = ()
    # Whether a person has answered the fulfillment question, including the
    # valid answer "this purchase fulfilled none of them".
    fulfillment_reviewed: bool = False

    readings: tuple[PriceReading, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "verdict", _verdict(self.verdict))
        object.__setattr__(
            self,
            "matched_interests",
            _unique_interest_refs(self.matched_interests, field_name="matched_interests"),
        )
        object.__setattr__(
            self,
            "fulfilled_interests",
            _unique_interest_refs(
                self.fulfilled_interests, field_name="fulfilled_interests"
            ),
        )
        if not isinstance(self.fulfillment_reviewed, bool):
            raise ValueError("fulfillment_reviewed must be true or false")
        # Version-2 files written before this flag existed already used a
        # nonempty allocation as proof that a person answered the question.
        if self.fulfilled_interests and not self.fulfillment_reviewed:
            object.__setattr__(self, "fulfillment_reviewed", True)
        matched_ids = {
            reference.interest_id.casefold()
            for reference in self.matched_interests
        }
        unrecognized = [
            reference.name
            for reference in self.fulfilled_interests
            if reference.interest_id.casefold() not in matched_ids
        ]
        if unrecognized:
            raise ValueError(
                "fulfilled interests must be recorded matches: " + ", ".join(unrecognized)
            )
        if self.estimated_retail is not None:
            require_not_negative(self.estimated_retail, field_name="estimated_retail")
        if self.my_estimate is not None:
            require_not_negative(self.my_estimate, field_name="my_estimate")

    @property
    def uid(self) -> str:
        """The one name that identifies this lot, across every relisting of it."""
        return uid_of(self.source, self.inventory_id or self.listing_id)

    @property
    def auctions_seen(self) -> int:
        """How many separate auctions this item's trail covers."""
        return len({reading.listing_id for reading in self.readings if reading.listing_id})

    def answers_to(self, source: str, identifier: str) -> bool:
        """Match either name a person might have to hand.

        Someone reading the file has the item id; someone reading a URL has the
        auction id. Both should find the same entry.
        """
        return source == self.source and identifier in {
            self.inventory_id,
            self.listing_id,
            *(reading.listing_id for reading in self.readings),
        }

    @property
    def concerns(self) -> tuple[ConditionTag, ...]:
        """The condition tags that are not green, worst news first."""
        return tuple(tag for tag in self.conditions if tag.is_concerning)

    @property
    def condition_photo_url(self) -> str:
        """The photo of this actual lot, rather than the manufacturer's."""
        return self.photo_urls[-1] if self.photo_urls else ""

    @property
    def first(self) -> PriceReading | None:
        return self.readings[0] if self.readings else None

    @property
    def latest(self) -> PriceReading | None:
        return self.readings[-1] if self.readings else None

    @property
    def movement(self) -> Decimal | None:
        """How far the bid has travelled since the first look, if there were two."""
        if len(self.readings) < 2:
            return None
        return self.readings[-1].current_bid - self.readings[0].current_bid

    @property
    def headroom(self) -> Decimal | None:
        """What is left between the latest total and the person's own estimate.

        Negative means it has already cost more than they said it was worth,
        which is the number worth seeing before bidding again.
        """
        if self.my_estimate is None or self.latest is None:
            return None
        return self.my_estimate - self.latest.total_cost


def uid_of(source: str, identifier: str) -> str:
    """Name one lot across providers; an id alone is only unique per site."""
    return f"{source}{UID_SEPARATOR}{identifier}"


def listing_key_of(source: str, listing_id: str) -> str:
    """The spelling a person copies out of a report and types back at a command.

    Deliberately not ``uid_of``: a uid may name the physical item, so that a
    relisted lot keeps one history, while a command has to act on the auction
    open today. Every report prints this one, so it is written once here.
    """
    return f"{source}{LISTING_KEY_SEPARATOR}{listing_id}"


def _verdict(verdict: Any) -> Verdict:
    """Accept either the word or the member, and return the member."""
    for allowed in Verdict:
        if verdict == allowed:
            return allowed
    choices = ", ".join(Verdict)
    raise ValueError(f"verdict must be one of: {choices}")


def _unique_interest_refs(
    references: tuple[InterestRef, ...], *, field_name: str
) -> tuple[InterestRef, ...]:
    """Keep one reference per stable id and reject ambiguous direct callers."""
    unique = []
    seen = set()
    for reference in references:
        if not isinstance(reference, InterestRef):
            raise ValueError(f"{field_name} must contain interest references")
        key = reference.interest_id.casefold()
        if key in seen:
            raise ValueError(f"{field_name} contains duplicate id: {reference.interest_id}")
        seen.add(key)
        unique.append(reference)
    return tuple(unique)


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


def _decidable(status: Any) -> LogisticsStatus:
    """Accept either the word or the member, and return the member.

    A saved decision arrives as text from SQLite and as an argument from the
    command line, so it is normalized here rather than at each call site. The
    frozen record is written through ``object.__setattr__`` because
    ``__post_init__`` runs after the field has already been assigned.
    """
    for allowed in OPERATOR_DECIDABLE:
        if status == allowed:
            return allowed
    choices = ", ".join(OPERATOR_DECIDABLE)
    raise ValueError(f"logistics decision must be one of: {choices}")


def _text(data: dict[str, Any], key: str) -> str:
    return str(data.get(key) or "").strip()
