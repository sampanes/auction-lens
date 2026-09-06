"""The analysis run, expressed without a command line.

Keeping this out of the CLI is what lets a run be exercised end to end from a
test, and what keeps argument parsing from acquiring opinions about scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from .config import AppConfig
from .models import Candidate, Listing, uid_of
from .scoring import evaluate
from .storage import LogisticsDecisionStore, ObservationStore, WatchlistStore
from .valuation import ValuationEngine


@dataclass(frozen=True)
class RunResult:
    """What one analysis run found, and what it deliberately ignored."""

    candidates: list[Candidate]
    listings_read: int
    listings_scored: int
    lots_followed: int = 0

    @property
    def listings_from_other_providers(self) -> int:
        return self.listings_read - self.listings_scored


def analyze_listings(
    listings: list[Listing],
    config: AppConfig,
    *,
    observations: ObservationStore,
    decisions: LogisticsDecisionStore,
    watchlist: WatchlistStore | None = None,
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
        lots_followed=_follow(candidates, watchlist),
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


def _follow(candidates: list[Candidate], watchlist: WatchlistStore | None) -> int:
    """Add one price reading per reported lot to the person's own file."""
    if watchlist is None:
        return 0
    return watchlist.record(_one_entry_per_lot(candidates))


def _one_entry_per_lot(candidates: list[Candidate]) -> list[tuple[Listing, Decimal]]:
    """Collapse a lot that matched several rules down to a single reading.

    Total cost is a property of the lot and the configured fees, not of the rule
    that noticed it, so the first match speaks for all of them.
    """
    seen: dict[str, tuple[Listing, Decimal]] = {}
    for candidate in candidates:
        listing = candidate.listing
        uid = uid_of(listing.source, listing.listing_id)
        seen.setdefault(uid, (listing, candidate.total_cost))
    return list(seen.values())
