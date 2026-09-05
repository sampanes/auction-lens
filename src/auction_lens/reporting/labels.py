"""Wording shared by the plain-text and HTML reports.

Both renderings describe the same findings, so the words come from here and
only the layout differs between them.
"""

from __future__ import annotations

from ..models import Candidate, ObservationChange

NEW_LABEL = "New"
PRICE_CHANGED_LABEL = "Price changed"
SEEN_LABEL = "Seen"

EMPTY_REPORT = "Auction Lens found no listings meeting the configured criteria."


def change_label(change: ObservationChange) -> str:
    """Say how this listing relates to what the database already knew."""
    if change.is_new:
        return NEW_LABEL
    if change.price_changed:
        return PRICE_CHANGED_LABEL
    return SEEN_LABEL


def readable(identifier: str) -> str:
    """Turn a stored identifier such as needs_plan into Needs Plan."""
    return identifier.replace("_", " ").title()


def decision_key(candidate: Candidate) -> str:
    """The exact key the logistics command expects for this listing."""
    return f"{candidate.listing.source}/{candidate.listing.listing_id}"


def by_score(candidates: list[Candidate]) -> list[Candidate]:
    """Order findings the way a person reads them: best first."""
    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
