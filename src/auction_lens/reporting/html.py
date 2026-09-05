"""The HTML report sent as the alternative part of the email.

Styles are inline because mail clients routinely discard a stylesheet, so the
few rules used here are named constants rather than repeated literals.
"""

from __future__ import annotations

from html import escape
from typing import Iterator

from ..logistics import NEEDS_PLAN, ORDINARY
from ..models import Candidate, ValuationBand
from .labels import EMPTY_REPORT, by_score, change_label, decision_key, readable

CARD_STYLE = "border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0"
HEADING_STYLE = "margin-top:0"
SEPARATOR = " &middot; "
RANGE_DASH = "&ndash;"


def render_html(candidates: list[Candidate]) -> str:
    """Render every candidate as a card, strongest first."""
    if not candidates:
        return f"<p>{EMPTY_REPORT}</p>"
    cards = "".join(_card(candidate) for candidate in by_score(candidates))
    return f"<h2>Auction Lens: {len(candidates)} match(es)</h2>{cards}"


def _card(candidate: Candidate) -> str:
    listing = candidate.listing
    return "".join(
        (
            f"<article style='{CARD_STYLE}'>",
            f"<h3 style='{HEADING_STYLE}'>{escape(listing.title)}</h3>",
            f"<p><strong>{_headline(candidate)}</strong></p>",
            f"<p>{_prices(candidate)}</p>",
            f"<p>{escape('; '.join(candidate.reasons))}</p>",
            _logistics(candidate),
            _valuation(candidate),
            f"<p><a href='{escape(listing.url, quote=True)}'>View listing</a></p>",
            "</article>",
        )
    )


def _headline(candidate: Candidate) -> str:
    return SEPARATOR.join(
        (
            candidate.category.title(),
            f"Score {candidate.score}",
            change_label(candidate.change),
        )
    )


def _prices(candidate: Candidate) -> str:
    listing = candidate.listing
    parts = [f"Bid ${listing.current_bid}", f"Estimated total ${candidate.total_cost}"]
    if listing.estimated_retail:
        parts.append(f"Retail ${listing.estimated_retail}")
    return SEPARATOR.join(parts)


def _logistics(candidate: Candidate) -> str:
    assessment = candidate.logistics
    if not assessment or assessment.status == ORDINARY:
        return ""
    if assessment.status == NEEDS_PLAN:
        questions = _list_items(escape(question) for question in assessment.questions)
        key = escape(decision_key(candidate))
        return (
            f"<p><strong>Logistics check</strong></p>{questions}"
            f"<p>Decision key: {key}</p>"
        )
    note = f"{SEPARATOR}{escape(assessment.decision_note)}" if assessment.decision_note else ""
    return f"<p><strong>Logistics:</strong> {escape(readable(assessment.status))}{note}</p>"


def _valuation(candidate: Candidate) -> str:
    valuation = candidate.valuation
    if not valuation:
        return ""
    sections = []
    if valuation.bands:
        sections.append(_list_items(_band_text(band) for band in valuation.bands))
    if valuation.research_links:
        links = " | ".join(
            f"<a href='{escape(link.url, quote=True)}'>{escape(link.label)}</a>"
            for link in valuation.research_links
        )
        sections.append(f"<p>Research: {links}</p>")
    if valuation.errors:
        sections.append("<p>Valuation sources unavailable:</p>")
        sections.append(_list_items(escape(error) for error in valuation.errors))
    return "".join(sections)


def _band_text(band: ValuationBand) -> str:
    return (
        f"{escape(readable(band.basis))}: ${band.low}{RANGE_DASH}${band.high} "
        f"(typical ${band.typical}; {band.source_count} source(s), "
        f"{band.sample_size} comp(s))"
    )


def _list_items(items: Iterator[str]) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
