"""Rendering-independent records for one Auction Lens report.

The builder decides what belongs in these records. Every delivery channel then
receives the same ``Report``, so changing presentation cannot quietly change
the meaning of the shared facts.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..listings.conditions import Tag
from ..matching.searches import SearchHint

# How many withheld titles a report names before it summarises the rest. Enough
# to recognise what is missing, few enough to stay a footnote.
NAMED_UNCHANGED = 8


@dataclass(frozen=True)
class Fact:
    """One labelled value about a listing, such as "Bid" and "$18.00"."""

    label: str
    value: str


@dataclass(frozen=True)
class ListingFacts:
    """The listing values every report medium receives from one projection."""

    bid: str
    total_cost: str
    retail: str
    retail_ratio: str
    closes: str
    location: str
    conditions: str
    condition_severity: Tag
    watch_key: str

    @property
    def full_report(self) -> tuple[Fact, ...]:
        """The complete labelled row used by text and HTML reports."""
        facts = [
            Fact("Bid", self.bid),
            Fact("Estimated total", self.total_cost),
        ]
        if self.retail:
            facts.append(Fact("Retail", self.retail))
        if self.closes:
            facts.append(Fact("Closes", self.closes))
        facts.extend(
            (
                Fact("Location", self.location or "unknown"),
                Fact("Conditions", self.conditions),
                Fact("Watch key", self.watch_key),
            )
        )
        return tuple(facts)


@dataclass(frozen=True)
class Link:
    """Somewhere a person can go to learn more."""

    label: str
    url: str


@dataclass(frozen=True)
class Photo:
    """One remotely hosted listing image, already named for a reader."""

    label: str
    url: str


@dataclass(frozen=True)
class Handling:
    """What still has to be said about getting this item home."""

    summary: str = ""
    note: str = ""
    questions: tuple[str, ...] = ()
    decision_key: str = ""

    @property
    def is_silent(self) -> bool:
        """Most lots need no handling thought at all, and say nothing."""
        return not self.summary and not self.questions


@dataclass(frozen=True)
class Valuation:
    """What the price sources said, already worded."""

    bands: tuple[str, ...] = ()
    research: tuple[Link, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_silent(self) -> bool:
        return not (self.bands or self.research or self.warnings)


@dataclass(frozen=True)
class Finding:
    """One listing worth reporting, in words but not in any particular format."""

    title: str
    change: str
    score: int
    priority_rank: int
    facts: ListingFacts
    reasons: tuple[str, ...]
    url: str
    photos: tuple[Photo, ...]
    handling: Handling
    valuation: Valuation


@dataclass(frozen=True)
class Group:
    """One kind of thing: the best few of it, and how to see the rest.

    A section is complete in itself. If the report is holding lots back, the
    count that says so and the phrase that reaches them belong here, beside the
    cards they are about, rather than in a footer the reader has to reassemble.
    """

    title: str
    findings: tuple[Finding, ...]
    # How many of this kind matched today but are not printed above.
    withheld: int = 0
    # Ways to reach this kind at the provider's end, for when some are withheld.
    searches: tuple[SearchHint, ...] = ()

    @property
    def is_crowded(self) -> bool:
        return self.withheld > 0


@dataclass(frozen=True)
class OutcomeSummary:
    """Finite wants and any outcome bookkeeping that still needs attention.

    These are complete reader-facing sentences so every delivery channel says
    the same thing. Renderers decide only whether a sentence is plain text or
    escaped markup.
    """

    progress: tuple[str, ...] = ()
    warning: str = ""

    @property
    def is_silent(self) -> bool:
        return not self.progress and not self.warning


@dataclass(frozen=True)
class DeliverySummary:
    """What destination-specific receipt filtering changed about this report."""

    active: bool = False
    repeated: bool = False
    unchanged_matches: int = 0
    held_back_matches: int = 0
    # Titles of the unchanged matches, so the count can be checked rather than
    # only believed. Optional: the watchlist route counts without naming, and
    # a count with no names still reads correctly.
    unchanged_titles: tuple[str, ...] = ()
    item_singular: str = "match"
    item_plural: str = "matches"

    def __post_init__(self) -> None:
        for field_name in ("unchanged_matches", "held_back_matches"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if len(self.unchanged_titles) > self.unchanged_matches:
            raise ValueError(
                "unchanged_titles cannot name more than unchanged_matches"
            )
        for field_name in ("item_singular", "item_plural"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty text")

    @property
    def lines(self) -> tuple[str, ...]:
        """Reader-facing facts shared by text, HTML, and webhook delivery."""
        if not self.active:
            return ()
        lines = [
            (
                "Delivery filter bypassed for this requested repeat."
                if self.repeated
                else f"Only new or price-changed {self.item_plural} are included "
                "in this delivery."
            )
        ]
        if self.unchanged_matches:
            noun = self._noun(self.unchanged_matches)
            verb = "was" if self.unchanged_matches == 1 else "were"
            lines.append(
                f"{self.unchanged_matches} unchanged {noun} {verb} already delivered here."
            )
            lines.extend(self._named_unchanged())
        if self.held_back_matches:
            noun = self._noun(self.held_back_matches)
            verb = "was" if self.held_back_matches == 1 else "were"
            lines.append(
                f"{self.held_back_matches} more new or changed {noun} {verb} held "
                "back by this report's limit."
            )
        return tuple(lines)

    def _named_unchanged(self) -> list[str]:
        """Name enough withheld lots to make their count actionable.

        A count alone leaves the reader asking which lots were omitted. The cap
        keeps that answer from crowding the report out of a chat message.
        """
        if not self.unchanged_titles:
            return []
        shown = [f"  - {title}" for title in self.unchanged_titles[:NAMED_UNCHANGED]]
        remaining = self.unchanged_matches - len(shown)
        if remaining:
            shown.append(f"  - and {remaining} more, unchanged since.")
        return shown

    def _noun(self, count: int) -> str:
        return self.item_singular if count == 1 else self.item_plural


NO_DELIVERY_FILTER = DeliverySummary()


@dataclass(frozen=True)
class Report:
    """One rendering-independent report built from a single set of findings.

    ``findings`` is the configured reading order used by compact reports.
    ``groups`` references those same immutable objects when a full report needs
    section headings. No renderer receives the scored candidates they came from.
    """

    headline: str
    findings: tuple[Finding, ...] = ()
    first_close: str = ""
    groups: tuple[Group, ...] = ()
    searches: tuple[SearchHint, ...] = ()
    notices: tuple[str, ...] = ()
    outcomes: OutcomeSummary = OutcomeSummary()
    delivery: DeliverySummary = NO_DELIVERY_FILTER

    @property
    def match_count(self) -> int:
        return len(self.findings)

    @property
    def is_empty(self) -> bool:
        return not self.findings
