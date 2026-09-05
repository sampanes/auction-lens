"""The plain-text report, which is also the body of every email.

It is written to be read in a terminal or on a phone: one block per finding,
strongest first, with the decision key an operator needs to answer a question.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterator

from ..logistics import NEEDS_PLAN, ORDINARY
from ..models import Candidate
from .labels import EMPTY_REPORT, by_score, change_label, decision_key, readable


def render_text(candidates: list[Candidate]) -> str:
    """Render every candidate, grouped by category and ordered by score."""
    if not candidates:
        return EMPTY_REPORT + "\n"
    lines = [f"Auction Lens found {len(candidates)} match(es)."]
    for category, items in _grouped_by_category(candidates).items():
        lines.extend(("", category.upper()))
        for item in items:
            lines.extend(_candidate_lines(item))
    return "\n".join(lines).rstrip() + "\n"


def _grouped_by_category(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in by_score(candidates):
        grouped[candidate.category].append(candidate)
    return grouped


def _candidate_lines(candidate: Candidate) -> Iterator[str]:
    listing = candidate.listing
    yield f"[{change_label(candidate.change).upper()}] {listing.title}"
    yield (
        f"Score {candidate.score} | bid ${listing.current_bid} "
        f"| estimated total ${candidate.total_cost}"
    )
    yield (
        f"Location: {listing.location or 'unknown'} "
        f"| Conditions: {', '.join(listing.conditions) or 'none listed'}"
    )
    yield f"Why: {'; '.join(candidate.reasons)}"
    yield from _logistics_lines(candidate)
    yield listing.url
    yield from _valuation_lines(candidate)
    yield ""


def _logistics_lines(candidate: Candidate) -> Iterator[str]:
    assessment = candidate.logistics
    if not assessment or assessment.status == ORDINARY:
        return
    if assessment.status == NEEDS_PLAN:
        for question in assessment.questions:
            yield f"LOGISTICS CHECK: {question}"
        yield f"Decision key: {decision_key(candidate)}"
        return
    note = f" | {assessment.decision_note}" if assessment.decision_note else ""
    yield f"Logistics: {readable(assessment.status)}{note}"


def _valuation_lines(candidate: Candidate) -> Iterator[str]:
    valuation = candidate.valuation
    if not valuation:
        return
    for band in valuation.bands:
        yield (
            f"{readable(band.basis)}: ${band.low}-${band.high} "
            f"(typical ${band.typical}; {band.source_count} source(s), "
            f"{band.sample_size} comp(s))"
        )
    if valuation.research_links:
        research = " | ".join(f"{link.label}: {link.url}" for link in valuation.research_links)
        yield f"Research: {research}"
    for error in valuation.errors:
        yield f"Valuation source unavailable: {error}"
