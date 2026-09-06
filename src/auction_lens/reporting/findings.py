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

from ..models import Candidate, LogisticsStatus, ValuationBand, ValuationSummary

EMPTY_REPORT = "Auction Lens found no listings meeting the configured criteria."

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

    @property
    def is_empty(self) -> bool:
        return not self.groups


def build_report(candidates: list[Candidate]) -> Report:
    """Turn scored candidates into everything a report has to say about them."""
    if not candidates:
        return Report(headline=EMPTY_REPORT)
    return Report(
        headline=f"Auction Lens found {len(candidates)} match(es).",
        groups=tuple(
            Group(title=category, findings=tuple(_finding(item) for item in items))
            for category, items in _by_category(candidates).items()
        ),
    )


def readable(identifier: str) -> str:
    """Turn a stored identifier such as needs_plan into Needs Plan."""
    return identifier.replace("_", " ").title()


def _by_category(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    """Group findings, ordering both the groups and their contents by score."""
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        grouped[str(candidate.category)].append(candidate)
    return grouped


def _finding(candidate: Candidate) -> Finding:
    return Finding(
        title=candidate.listing.title,
        change=_change(candidate),
        score=candidate.score,
        facts=_facts(candidate),
        reasons=candidate.reasons,
        url=candidate.listing.url,
        handling=_handling(candidate),
        valuation=_valuation(candidate.valuation),
    )


def _change(candidate: Candidate) -> str:
    """Say how this listing relates to what the database already knew."""
    if candidate.change.is_new:
        return NEW_LABEL
    if candidate.change.price_changed:
        return PRICE_CHANGED_LABEL
    return SEEN_LABEL


def _facts(candidate: Candidate) -> tuple[Fact, ...]:
    listing = candidate.listing
    facts = [
        Fact("Bid", f"${listing.current_bid}"),
        Fact("Estimated total", f"${candidate.total_cost}"),
    ]
    if listing.estimated_retail:
        facts.append(Fact("Retail", f"${listing.estimated_retail}"))
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
