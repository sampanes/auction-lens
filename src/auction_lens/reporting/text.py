"""The plain-text report, which is also the body of every email.

It is written to be read in a terminal or on a phone: one block per finding,
strongest first, with the decision key an operator needs to answer a question.
This module decides layout only. What the report says comes from ``findings``.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..models import Candidate
from .findings import Fact, Finding, Handling, Report, Valuation, build_report

SEPARATOR = " | "

# Enough facts per line to stay compact, few enough to read on a phone.
FACTS_PER_LINE = 3


def render_text(candidates: list[Candidate]) -> str:
    """Render every candidate, grouped by category and ordered by score."""
    return _as_text(build_report(candidates))


def _as_text(report: Report) -> str:
    if report.is_empty:
        return report.headline + "\n"
    lines = [report.headline]
    for group in report.groups:
        lines.extend(("", group.title.upper()))
        for finding in group.findings:
            lines.extend(_finding_lines(finding))
    return "\n".join(lines).rstrip() + "\n"


def _finding_lines(finding: Finding) -> Iterator[str]:
    yield f"[{finding.change.upper()}] {finding.title}"
    yield f"Score {finding.score}"
    yield from _fact_lines(finding.facts)
    yield f"Why: {'; '.join(finding.reasons)}"
    yield from _handling_lines(finding.handling)
    yield finding.url
    yield from _valuation_lines(finding.valuation)
    yield ""


def _fact_lines(facts: tuple[Fact, ...]) -> Iterator[str]:
    """Wrap the facts a few to a line rather than one very long one."""
    for start in range(0, len(facts), FACTS_PER_LINE):
        row = facts[start : start + FACTS_PER_LINE]
        yield SEPARATOR.join(f"{fact.label}: {fact.value}" for fact in row)


def _handling_lines(handling: Handling) -> Iterator[str]:
    if handling.is_silent:
        return
    for question in handling.questions:
        yield f"LOGISTICS CHECK: {question}"
    if handling.decision_key:
        yield f"Decision key: {handling.decision_key}"
    if handling.summary:
        note = f"{SEPARATOR}{handling.note}" if handling.note else ""
        yield f"Logistics: {handling.summary}{note}"


def _valuation_lines(valuation: Valuation) -> Iterator[str]:
    yield from valuation.bands
    if valuation.research:
        links = SEPARATOR.join(f"{link.label}: {link.url}" for link in valuation.research)
        yield f"Research: {links}"
    for warning in valuation.warnings:
        yield f"Valuation source unavailable: {warning}"
