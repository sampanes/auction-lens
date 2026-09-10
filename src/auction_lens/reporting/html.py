"""The HTML report sent as the alternative part of the email.

Styles are inline because mail clients routinely discard a stylesheet, so the
few rules used here are named constants rather than repeated literals. This
module decides markup only. What the report says comes from ``findings``.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape
from zoneinfo import ZoneInfo

from ..models import Candidate, InterestProgress, ReadingOrder
from .findings import (
    Fact,
    Finding,
    Handling,
    OutcomeSummary,
    Photo,
    Report,
    Valuation,
    build_report,
)
from .searches import SearchHint

CARD_STYLE = "border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0"
HEADING_STYLE = "margin-top:0"
PHOTOS_STYLE = "width:100%;border-collapse:collapse;table-layout:fixed;margin:12px 0"
PHOTO_CELL_STYLE = "vertical-align:top;padding:0 4px"
PHOTO_LABEL_STYLE = "color:#666;font-size:12px;margin:0 0 4px 0"
PHOTO_STYLE = (
    "display:block;width:100%;height:auto;max-height:220px;object-fit:contain;"
    "border:0;border-radius:6px"
)
SEPARATOR = " &middot; "

# Worded as an instruction rather than as another heading, because it is one.
SEARCH_HEADING = "Paste into the site search to see a whole category:"
SEARCH_RULE_STYLE = "margin:12px 0 4px 0;font-weight:bold"
SEARCH_NOTE_STYLE = "color:#666;font-size:12px"
OUTCOME_WARNING_STYLE = (
    "border-left:4px solid #c62828;background:#fff4f4;padding:10px;margin:12px 0"
)


def render_html(
    candidates: list[Candidate],
    zone: ZoneInfo,
    searches: tuple[SearchHint, ...] = (),
    order: ReadingOrder = ReadingOrder.PRIORITY,
    interest_progress: tuple[InterestProgress, ...] = (),
    unreviewed_wins: int = 0,
) -> str:
    """Render every candidate as a card, strongest first."""
    return _as_html(
        build_report(
            candidates,
            zone,
            searches,
            order,
            interest_progress,
            unreviewed_wins,
        )
    )


def _as_html(report: Report) -> str:
    if report.is_empty:
        sections = [f"<p>{escape(report.headline)}</p>"]
    else:
        sections = [f"<h2>{escape(report.headline)}</h2>"]
    sections.append(_outcomes(report.outcomes))
    for group in report.groups:
        sections.append(f"<h3>{escape(group.title.title())}</h3>")
        sections.extend(_card(finding) for finding in group.findings)
    sections.append(_searches(report))
    return "".join(sections)


def _outcomes(outcomes: OutcomeSummary) -> str:
    """Render the shared wording, emphasizing bookkeeping that needs a person."""
    if outcomes.is_silent:
        return ""
    parts = []
    if outcomes.warning:
        parts.append(
            f"<p style='{OUTCOME_WARNING_STYLE}'><strong>"
            f"{escape(outcomes.warning)}</strong></p>"
        )
    if outcomes.progress:
        parts.append("<h3>Interest progress</h3>")
        parts.append(_list_items(escape(status) for status in outcomes.progress))
    return "".join(parts)


def _searches(report: Report) -> str:
    """The paste-able phrases, as text a phone will let you select and copy.

    Deliberately not links: the point is to arrive at the provider's search
    with the words in the box, so that the next search can be edited by hand.
    """
    if not report.searches:
        return ""
    rules = dict.fromkeys(hint.rule for hint in report.searches)
    blocks = [f"<h3>{escape(SEARCH_HEADING)}</h3>"]
    for rule in rules:
        blocks.append(f"<p style='{SEARCH_RULE_STYLE}'>{escape(rule)}</p><ul>")
        for hint in report.searches:
            if hint.rule == rule:
                blocks.append(
                    f"<li><code>{escape(hint.phrase)}</code> "
                    f"<span style='{SEARCH_NOTE_STYLE}'>{escape(_note(hint))}</span></li>"
                )
        blocks.append("</ul>")
    return "".join(blocks)


def _note(hint: SearchHint) -> str:
    """What the phrase costs, said plainly enough to decide by."""
    if not hint.also_finds:
        return f"finds {hint.finds}, nothing else"
    return f"finds {hint.finds}, plus {hint.also_finds} other lot(s)"


def _card(finding: Finding) -> str:
    return "".join(
        (
            f"<article style='{CARD_STYLE}'>",
            f"<h4 style='{HEADING_STYLE}'>{escape(finding.title)}</h4>",
            f"<p><strong>Score {finding.score}{SEPARATOR}{escape(finding.change)}</strong></p>",
            f"<p>{_facts(finding.facts)}</p>",
            f"<p>{escape('; '.join(finding.reasons))}</p>",
            _photos(finding),
            _handling(finding.handling),
            _valuation(finding.valuation),
            f"<p><a href='{escape(finding.url, quote=True)}'>View listing</a></p>",
            "</article>",
        )
    )


def _photos(finding: Finding) -> str:
    if not finding.photos:
        return ""
    listing_url = escape(finding.url, quote=True)
    width = 100 // len(finding.photos)
    cells = "".join(_photo_cell(photo, listing_url, width) for photo in finding.photos)
    return (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"style='{PHOTOS_STYLE}'><tr>{cells}</tr></table>"
    )


def _photo_cell(photo: Photo, listing_url: str, width: int) -> str:
    label = escape(photo.label)
    photo_url = escape(photo.url, quote=True)
    return (
        f"<td width='{width}%' style='{PHOTO_CELL_STYLE}'>"
        f"<p style='{PHOTO_LABEL_STYLE}'><strong>{label}</strong></p>"
        f"<a href='{listing_url}'>"
        f"<img src='{photo_url}' alt='{label}' style='{PHOTO_STYLE}'></a></td>"
    )


def _facts(facts: tuple[Fact, ...]) -> str:
    return SEPARATOR.join(
        f"{escape(fact.label)}: {escape(fact.value)}" for fact in facts
    )


def _handling(handling: Handling) -> str:
    if handling.is_silent:
        return ""
    parts = []
    if handling.questions:
        parts.append("<p><strong>Logistics check</strong></p>")
        parts.append(_list_items(escape(question) for question in handling.questions))
    if handling.decision_key:
        parts.append(f"<p>Decision key: {escape(handling.decision_key)}</p>")
    if handling.summary:
        note = f"{SEPARATOR}{escape(handling.note)}" if handling.note else ""
        parts.append(f"<p><strong>Logistics:</strong> {escape(handling.summary)}{note}</p>")
    return "".join(parts)


def _valuation(valuation: Valuation) -> str:
    if valuation.is_silent:
        return ""
    parts = []
    if valuation.bands:
        parts.append(_list_items(escape(band) for band in valuation.bands))
    if valuation.research:
        links = SEPARATOR.join(
            f"<a href='{escape(link.url, quote=True)}'>{escape(link.label)}</a>"
            for link in valuation.research
        )
        parts.append(f"<p>Research: {links}</p>")
    if valuation.warnings:
        parts.append("<p>Valuation sources unavailable:</p>")
        parts.append(_list_items(escape(warning) for warning in valuation.warnings))
    return "".join(parts)


def _list_items(items: Iterable[str]) -> str:
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
