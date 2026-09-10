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
    LogisticsStatus,
    ReadingOrder,
    ValuationBand,
    ValuationSummary,
    ranked,
)
from .searches import SearchHint

EMPTY_REPORT = "Auction Lens found no listings meeting the configured criteria."

# Day, hour, and the zone's own name: enough to act on, short enough to sit on
# one line. The zone is named because a report is read wherever the reader is.
CLOSING_TIME_FORMAT = "%a %H:%M %Z"

NEW_LABEL = "New"
PRICE_CHANGED_LABEL = "Price changed"
SEEN_LABEL = "Seen"

NO_LOCATION = "unknown"
NO_CONDITIONS = "none listed"


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
    """Findings that share a reason for being reported."""

    title: str
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class Report:
    """One rendering-independent report."""

    headline: str
    groups: tuple[Group, ...] = ()
    # Ways to reach the same lots at the provider's end, for the categories
    # the report found too many of to click through one at a time.
    searches: tuple[SearchHint, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.groups


def build_report(
    candidates: list[Candidate],
    zone: ZoneInfo,
    searches: tuple[SearchHint, ...] = (),
    order: ReadingOrder = ReadingOrder.PRIORITY,
) -> Report:
    """Turn scored candidates into everything a report has to say about them.

    The zone is the provider's, because a closing time is a fact about the
    auction rather than about whoever opens the mail.
    """
    if not candidates:
        return Report(headline=EMPTY_REPORT)
    return Report(
        headline=f"Auction Lens found {len(candidates)} match(es).",
        searches=searches,
        groups=tuple(
            Group(
                title=category,
                findings=tuple(_finding(item, zone) for item in items),
            )
            for category, items in _by_category(candidates, order).items()
        ),
    )


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


def _by_category(
    candidates: list[Candidate], order: ReadingOrder
) -> dict[str, list[Candidate]]:
    """Group findings, ordering both the groups and their contents to read.

    Ordering only, never selection: which lots are worth reporting was
    already decided against the bars, and a reader preferring to see the
    dearest thing first must not quietly change what reached the page.
    """
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in ranked(candidates, order=order):
        grouped[str(candidate.category)].append(candidate)
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
    return tuple(facts)


def _handling(candidate: Candidate) -> Handling:
    """Ask an open question, report a settled one, or say nothing at all."""
    assessment = candidate.logistics
    if assessment is None or assessment.status == LogisticsStatus.ORDINARY:
        return Handling()
    if assessment.status == LogisticsStatus.NEEDS_PLAN:
        return Handling(
            questions=assessment.questions,
            decision_key=_decision_key(candidate),
        )
    return Handling(
        summary=readable(assessment.status),
        note=assessment.decision_note,
    )


def _decision_key(candidate: Candidate) -> str:
    """The exact key the logistics command expects for this listing."""
    return f"{candidate.listing.source}/{candidate.listing.listing_id}"


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
