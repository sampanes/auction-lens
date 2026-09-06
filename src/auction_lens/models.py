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

# The scale every score lives on. Scoring clamps to it and configuration is
# checked against it, so both read it from the record they are talking about.
LOWEST_SCORE = 0
HIGHEST_SCORE = 100


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
    rule_name: str
    score: int
    total_cost: Decimal
    retail_ratio: Decimal | None
    reasons: tuple[str, ...]
    change: ObservationChange
    valuation: ValuationSummary | None = None
    logistics: LogisticsAssessment | None = None


@dataclass(frozen=True)
class PriceReading:
    """One look at a lot: what it cost at that moment.

    A scan every hour leaves twenty-four of these in a day; a scan once leaves
    one. That is the whole point of keeping them as a list rather than as a
    single "current price" that forgets everything it replaces.
    """

    scanned_at: datetime
    current_bid: Decimal
    total_cost: Decimal
    bid_count: int = 0

    def __post_init__(self) -> None:
        require_not_negative(self.current_bid, field_name="current_bid")
        require_not_negative(self.total_cost, field_name="total_cost")
        require_not_negative(self.bid_count, field_name="bid_count")


@dataclass(frozen=True)
class WatchedItem:
    """One lot a person is following, and every look they have taken at it.

    The first block is what the provider said, refreshed on every run. The
    second block is what the person thinks, which no run may overwrite.
    """

    source: str
    listing_id: str
    title: str = ""
    url: str = ""
    photo_urls: tuple[str, ...] = ()
    estimated_retail: Decimal | None = None
    conditions: tuple[ConditionTag, ...] = ()
    quality_rating: int | None = None

    my_estimate: Decimal | None = None
    verdict: Verdict = Verdict.WATCHING
    note: str = ""

    readings: tuple[PriceReading, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "verdict", _verdict(self.verdict))
        if self.estimated_retail is not None:
            require_not_negative(self.estimated_retail, field_name="estimated_retail")
        if self.my_estimate is not None:
            require_not_negative(self.my_estimate, field_name="my_estimate")

    @property
    def uid(self) -> str:
        """The one name that identifies this lot everywhere in the project."""
        return uid_of(self.source, self.listing_id)

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


def uid_of(source: str, listing_id: str) -> str:
    """Name one lot across providers; a listing id alone is only unique per site."""
    return f"{source}{UID_SEPARATOR}{listing_id}"


def _verdict(verdict: Any) -> Verdict:
    """Accept either the word or the member, and return the member."""
    for allowed in Verdict:
        if verdict == allowed:
            return allowed
    choices = ", ".join(Verdict)
    raise ValueError(f"verdict must be one of: {choices}")


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
