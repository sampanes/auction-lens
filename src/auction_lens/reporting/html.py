"""The HTML report sent as the alternative part of the email.

Styles are inline because mail clients routinely discard a stylesheet, so the
few rules used here are named constants rather than repeated literals. This
module decides markup only. What the report says comes from ``findings``.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape

from ..models import Candidate
from .findings import Fact, Finding, Handling, Report, Valuation, build_report

CARD_STYLE = "border:1px solid #ddd;border-radius:8px;padding:14px;margin:12px 0"
HEADING_STYLE = "margin-top:0"
SEPARATOR = " &middot; "


def render_html(candidates: list[Candidate]) -> str:
    """Render every candidate as a card, strongest first."""
    return _as_html(build_report(candidates))


def _as_html(report: Report) -> str:
    if report.is_empty:
        return f"<p>{escape(report.headline)}</p>"
    sections = [f"<h2>{escape(report.headline)}</h2>"]
    for group in report.groups:
        sections.append(f"<h3>{escape(group.title.title())}</h3>")
        sections.extend(_card(finding) for finding in group.findings)
    return "".join(sections)


def _card(finding: Finding) -> str:
    return "".join(
        (
            f"<article style='{CARD_STYLE}'>",
            f"<h4 style='{HEADING_STYLE}'>{escape(finding.title)}</h4>",
            f"<p><strong>Score {finding.score}{SEPARATOR}{escape(finding.change)}</strong></p>",
            f"<p>{_facts(finding.facts)}</p>",
            f"<p>{escape('; '.join(finding.reasons))}</p>",
            _handling(finding.handling),
            _valuation(finding.valuation),
            f"<p><a href='{escape(finding.url, quote=True)}'>View listing</a></p>",
            "</article>",
        )
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
