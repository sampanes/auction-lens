"""What a report says, decided once, before anything decides how it looks.

The plain-text and HTML reports describe the same findings. When each of them
walked a candidate itself, they were free to drift: one of them showed stated
retail, the other showed the pickup location, and nothing noticed. So the
question "what does the report say" is answered here, exactly once, and a
renderer only answers "what does that look like in this medium".

Nothing in this module knows about terminals, markup, or escaping.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import (
    Candidate,
    InterestHarvest,
    InterestProgress,
    LogisticsStatus,
    ReadingOrder,
    ValuationBand,
    ValuationSummary,
    ranked,
)
from .searches import SearchHint

EMPTY_REPORT = "Auction Lens found no listings meeting the configured criteria."
EMPTY_DELIVERY = "Auction Lens found no new or price-changed listings for this destination."

# Day, hour, and the zone's own name: enough to act on, short enough to sit on
# one line. The zone is named because a report is read wherever the reader is.
CLOSING_TIME_FORMAT = "%a %H:%M %Z"

NEW_LABEL = "New"
PRICE_CHANGED_LABEL = "Price changed"
SEEN_LABEL = "Seen"

NO_LOCATION = "unknown"
NO_CONDITIONS = "none listed"

UNREVIEWED_WIN = (
    "Action needed: {count} won {lots} {have} an unreviewed finite-interest "
    "match; review {them} with watchlist --verdict won, then use watch "
    "--fulfills or watch --clear-fulfillments."
)


@dataclass(frozen=True)
class Fact:
    """One labelled value about a listing, such as "Bid" and "$18.00"."""

    label: str
    value: str


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
    facts: tuple[Fact, ...]
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
    item_singular: str = "match"
    item_plural: str = "matches"

    def __post_init__(self) -> None:
        for field_name in ("unchanged_matches", "held_back_matches"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
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
        if self.held_back_matches:
            noun = self._noun(self.held_back_matches)
            verb = "was" if self.held_back_matches == 1 else "were"
            lines.append(
                f"{self.held_back_matches} more new or changed {noun} {verb} held "
                "back by this report's limit."
            )
        return tuple(lines)

    def _noun(self, count: int) -> str:
        return self.item_singular if count == 1 else self.item_plural


NO_DELIVERY_FILTER = DeliverySummary()


@dataclass(frozen=True)
class Report:
    """One rendering-independent report, and what it was built from.

    Three renderers read this: text, HTML, and a chat webhook. The first two
    want the worded groups below. The third arranges its own cards and wants
    the scored lots, so they are carried here rather than threaded alongside
    this record as a second argument everything has to keep in step.

    Building it is what ``build_report`` is for, and doing so is the only place
    that has to get the order of these facts right. Before that, six functions
    took the same eight values as positional arguments -- and two of them took
    them in different orders.
    """

    headline: str
    zone: ZoneInfo
    # What the report was built from, for a renderer that words lots itself.
    candidates: tuple[Candidate, ...] = ()
    order: ReadingOrder = ReadingOrder.PRIORITY
    groups: tuple[Group, ...] = ()
    # Ways to reach the same lots at the provider's end, for the categories
    # the report found too many of to click through one at a time.
    searches: tuple[SearchHint, ...] = ()
    outcomes: OutcomeSummary = OutcomeSummary()
    delivery: DeliverySummary = NO_DELIVERY_FILTER

    @property
    def is_empty(self) -> bool:
        return not self.groups


def build_report(
    candidates: list[Candidate],
    zone: ZoneInfo,
    *,
    searches: tuple[SearchHint, ...] = (),
    order: ReadingOrder = ReadingOrder.PRIORITY,
    interest_progress: tuple[InterestProgress, ...] = (),
    unreviewed_wins: int = 0,
    delivery: DeliverySummary = NO_DELIVERY_FILTER,
    harvest: tuple[InterestHarvest, ...] = (),
) -> Report:
    """Turn scored candidates into everything a report has to say about them.

    The zone is the provider's, because a closing time is a fact about the
    auction rather than about whoever opens the mail.

    Everything after it must be named. This is the only function left that
    takes the whole bundle, so it is the only place a caller could put two of
    them the wrong way round, and naming them makes that impossible rather
    than merely unlikely.
    """
    outcomes = build_outcome_summary(interest_progress, unreviewed_wins)
    if not candidates:
        headline = (
            EMPTY_DELIVERY
            if delivery.active and not delivery.repeated
            else EMPTY_REPORT
        )
        return Report(
            headline=headline, zone=zone, order=order,
            outcomes=outcomes, delivery=delivery,
        )
    sections = _by_section(candidates, order)
    return Report(
        headline=_headline(candidates, zone),
        zone=zone,
        candidates=tuple(candidates),
        order=order,
        searches=_hints_without_a_section(searches, set(sections)),
        outcomes=outcomes,
        delivery=delivery,
        groups=tuple(
            _group(name, items, zone, harvest, searches)
            for name, items in sections.items()
        ),
    )


def _group(
    name: str,
    items: list[Candidate],
    zone: ZoneInfo,
    harvest: tuple[InterestHarvest, ...],
    searches: tuple[SearchHint, ...],
) -> Group:
    """One section, carrying what it is not showing along with what it is."""
    withheld = next((tally.withheld for tally in harvest if tally.name == name), 0)
    return Group(
        title=readable(name),
        findings=tuple(_finding(item, zone) for item in items),
        withheld=withheld,
        # A phrase is only a shortcut when there is something to reach with it.
        searches=tuple(hint for hint in searches if hint.rule == name) if withheld else (),
    )


def _hints_without_a_section(
    searches: tuple[SearchHint, ...], sections: set[str]
) -> tuple[SearchHint, ...]:
    """Phrases for kinds that are not on the page at all.

    A rule with a section has already said everything it needs to say, in that
    section, whether or not it is holding anything back. This is the remainder:
    a rule that earned a phrase but whose lots did not survive the report's own
    cap. Without a footer those lots would be unreachable and unmentioned.
    """
    return tuple(hint for hint in searches if hint.rule not in sections)


def build_outcome_summary(
    progress: tuple[InterestProgress, ...], unreviewed_wins: int
) -> OutcomeSummary:
    """Say only what outcomes can establish without guessing intent.

    An unlimited interest has no finish line and therefore no useful progress
    fraction. A finite match is also never allocated implicitly: the warning
    asks the person who knows which want the purchase actually fulfilled.
    """
    finite = tuple(_progress_line(item) for item in progress if item.is_limited)
    warning = ""
    if unreviewed_wins:
        singular = unreviewed_wins == 1
        warning = UNREVIEWED_WIN.format(
            count=unreviewed_wins,
            lots="lot" if singular else "lots",
            have="has" if singular else "have",
            them="it" if singular else "them",
        )
    return OutcomeSummary(progress=finite, warning=warning)


def _progress_line(progress: InterestProgress) -> str:
    """A compact status for one finite want, using its remembered display name.

    The numerator counts explicit allocations on lots whose verdict is WON. It
    does not count every win, so name the human decision rather than the verdict.
    """
    wanted = progress.wanted
    if wanted is None:  # Kept total even if called independently in a future refactor.
        return ""
    state = "retired" if progress.is_retired else f"{progress.remaining} remaining"
    return (
        f"{progress.interest.name}: {progress.fulfilled}/{wanted} fulfilled; {state}"
    )


def _headline(candidates: list[Candidate], zone: ZoneInfo) -> str:
    """How many, and how long there is before the first one is gone.

    The deadline belongs in the first line because it is the only fact that
    decides whether the rest is worth reading now or after dinner.
    """
    soonest = soonest_close(candidates)
    if soonest is None:
        return f"Auction Lens found {len(candidates)} match(es)."
    return (
        f"Auction Lens found {len(candidates)} match(es); "
        f"the first closes {closing_time(soonest, zone)}."
    )


def soonest_close(candidates: list[Candidate]) -> datetime | None:
    """When the earliest-closing reported lot goes, or None if none says."""
    times = [
        candidate.listing.ends_at
        for candidate in candidates
        if candidate.listing.ends_at is not None
    ]
    return min(times) if times else None


def closing_time(ends_at: datetime | None, zone: ZoneInfo) -> str:
    """When bidding ends, in the provider's local time, or "" if unstated.

    Shared with the webhook so that both reports say a closing time the same
    way. Every lot seen so far states one, so an empty answer means the page
    changed shape rather than that this lot runs forever -- which is why
    nothing here invents a substitute for a time it was not given.
    """
    if ends_at is None:
        return ""
    return ends_at.astimezone(zone).strftime(CLOSING_TIME_FORMAT)


def readable(identifier: str) -> str:
    """Turn a stored identifier such as needs_plan into Needs Plan."""
    return identifier.replace("_", " ").title()


def _by_section(
    candidates: list[Candidate], order: ReadingOrder
) -> dict[str, list[Candidate]]:
    """Group findings by what they are one of, ordering the groups to read.

    Ordering only, never selection: which lots are worth reporting was
    already decided against the bars, and a reader preferring to see the
    dearest thing first must not quietly change what reached the page.

    Sections arrive in the order their best lot did, so the strongest thing
    found today is still the first thing read.
    """
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in ranked(candidates, order=order):
        grouped[candidate.section].append(candidate)
    return grouped


def _finding(candidate: Candidate, zone: ZoneInfo) -> Finding:
    return Finding(
        title=candidate.listing.title,
        change=_change(candidate),
        score=candidate.score,
        facts=_facts(candidate, zone),
        reasons=candidate.reasons,
        url=candidate.listing.url,
        photos=_photos(candidate),
        handling=_handling(candidate),
        valuation=_valuation(candidate.valuation),
    )


def _photos(candidate: Candidate) -> tuple[Photo, ...]:
    """Name the two useful ends of a gallery without showing one image twice."""
    stock = _https_photo(candidate.listing.stock_photo_url)
    actual = _https_photo(candidate.listing.condition_photo_url)
    if stock and stock == actual:
        # One photo in the gallery, so neither label would be a claim we can
        # make about it. Say only what is certain: it came from the listing.
        return (Photo("Listing photo", stock),)
    photos = []
    if stock:
        photos.append(Photo("Product photo", stock))
    if actual:
        photos.append(Photo("Actual lot", actual))
    return tuple(photos)


def _https_photo(url: str) -> str:
    """Keep email images remote and encrypted; omit anything else."""
    return url if url.lower().startswith("https://") else ""


def _change(candidate: Candidate) -> str:
    """Say how this listing relates to what the database already knew."""
    if candidate.change.is_new:
        return NEW_LABEL
    if candidate.change.price_changed:
        if candidate.change.previous_bid is not None:
            return f"{PRICE_CHANGED_LABEL} from ${candidate.change.previous_bid}"
        return PRICE_CHANGED_LABEL
    return SEEN_LABEL


def _facts(candidate: Candidate, zone: ZoneInfo) -> tuple[Fact, ...]:
    """The money first, then where and when the lot has to be dealt with."""
    listing = candidate.listing
    facts = [
        Fact("Bid", f"${listing.current_bid}"),
        Fact("Estimated total", f"${candidate.total_cost}"),
    ]
    if listing.estimated_retail:
        facts.append(Fact("Retail", f"${listing.estimated_retail}"))
    closes = closing_time(listing.ends_at, zone)
    if closes:
        facts.append(Fact("Closes", closes))
    facts.append(Fact("Location", listing.location or NO_LOCATION))
    facts.append(Fact("Conditions", ", ".join(listing.conditions) or NO_CONDITIONS))
    facts.append(Fact("Watch key", candidate.listing.key))
    return tuple(facts)


def _handling(candidate: Candidate) -> Handling:
    """Ask an open question, report a settled one, or say nothing at all."""
    assessment = candidate.logistics
    if assessment is None or assessment.status == LogisticsStatus.ORDINARY:
        return Handling()
    if assessment.status == LogisticsStatus.NEEDS_PLAN:
        return Handling(
            questions=assessment.questions,
            decision_key=candidate.listing.key,
        )
    return Handling(
        summary=readable(assessment.status),
        note=assessment.decision_note,
    )


def _valuation(summary: ValuationSummary | None) -> Valuation:
    if summary is None:
        return Valuation()
    return Valuation(
        bands=tuple(_band(band) for band in summary.bands),
        research=tuple(
            Link(label=link.label, url=link.url) for link in summary.research_links
        ),
        warnings=tuple(summary.errors),
    )


def _band(band: ValuationBand) -> str:
    return (
        f"{readable(band.basis)}: ${band.low}-${band.high} "
        f"(typical ${band.typical}; {band.source_count} source(s), "
        f"{band.sample_size} comp(s))"
    )
