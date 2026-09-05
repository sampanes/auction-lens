"""The analysis run, expressed without a command line.

Keeping this out of the CLI is what lets a run be exercised end to end from a
test, and what keeps argument parsing from acquiring opinions about scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .config import AppConfig
from .models import Candidate, Listing
from .scoring import evaluate
from .storage import LogisticsDecisionStore, ObservationStore
from .valuation import ValuationEngine


@dataclass(frozen=True)
class RunResult:
    """What one analysis run found, and what it deliberately ignored."""

    candidates: list[Candidate]
    listings_read: int
    listings_scored: int

    @property
    def listings_from_other_providers(self) -> int:
        return self.listings_read - self.listings_scored


def analyze_listings(
    listings: list[Listing],
    config: AppConfig,
    *,
    observations: ObservationStore,
    decisions: LogisticsDecisionStore,
    valuation_engine: ValuationEngine | None = None,
    now: datetime | None = None,
) -> RunResult:
    """Observe, score, and value every listing belonging to this provider.

    A file may hold listings from several providers, but one configuration
    describes one provider's fees and rules, so the rest are left alone.
    """
    candidates: list[Candidate] = []
    scored = 0
    for listing in listings:
        if listing.source != config.provider.provider_id:
            continue
        scored += 1
        matches = evaluate(
            listing,
            config,
            observations.observe(listing),
            logistics_decision=decisions.get(listing.source, listing.listing_id),
            now=now,
        )
        candidates.extend(_with_valuation(matches, listing, valuation_engine))
    return RunResult(
        candidates=candidates,
        listings_read=len(listings),
        listings_scored=scored,
    )


def _with_valuation(
    matches: list[Candidate],
    listing: Listing,
    engine: ValuationEngine | None,
) -> list[Candidate]:
    """Value a listing once, only when something about it is worth reporting."""
    if engine is None or not matches:
        return matches
    valuation = engine.value(listing)
    return [replace(candidate, valuation=valuation) for candidate in matches]
