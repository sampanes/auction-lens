"""The analysis run, expressed without a command line.

Keeping this out of the CLI is what lets a run be exercised end to end from a
test, and what keeps argument parsing from acquiring opinions about scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .config import AppConfig
from .models import Candidate, Listing, ranked, uid_of
from .reporting.searches import SearchHint, search_hints
from .scoring import evaluate
from .storage import LogisticsDecisionStore, ObservationStore, WatchlistStore
from .valuation import ValuationEngine


@dataclass(frozen=True)
class RunResult:
    """What one analysis run found, and what it deliberately ignored."""

    candidates: list[Candidate]
    listings_read: int
    listings_scored: int
    matches_found: int = 0
    lots_followed: int = 0
    # Lots left unscored because they cannot be acted on: already closed, or
    # closing further out than this report reaches. Counted so a short report
    # is never mistaken for a quiet day.
    lots_outside_the_window: int = 0
    # Ways to reach these lots at the provider's end. Built from everything
    # that matched rather than from the part that fitted, because reaching
    # what the cap held back is the whole reason to offer a phrase.
    searches: tuple[SearchHint, ...] = ()

    @property
    def listings_from_other_providers(self) -> int:
        """Everything read that this provider's configuration cannot speak for.

        Three things account for every listing read: another provider's, one
        outside the closing window, and one actually scored. Naming two of them
        leaves this one as the remainder.
        """
        return self.listings_read - self.listings_scored - self.lots_outside_the_window

    @property
    def matches_not_shown(self) -> int:
        """How many the cap held back, so a report can admit to hiding them."""
        return max(self.matches_found - len(self.candidates), 0)


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
    # One clock for the whole run. Resolving it per listing would let the
    # window a lot is measured against move while the run is under way.
    now = now or datetime.now(UTC)
    candidates: list[Candidate] = []
    scored = 0
    skipped = 0
    for listing in listings:
        if listing.source != config.provider.provider_id:
            continue
        if not still_worth_reading(listing, now, config.reports.closing_within_hours):
            skipped += 1
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

    # Ranked and capped once, here, so the printed report, the email, the chat
    # message, and the lots the watchlist starts following are all the same
    # report. Capping in each renderer instead would let them disagree.
    reportable = ranked(candidates, config.reports.max_items)
    return RunResult(
        candidates=reportable,
        searches=search_hints(candidates, listings, config.interests),
        listings_read=len(listings),
        listings_scored=scored,
        matches_found=len(candidates),
        lots_followed=_follow(reportable, watchlist),
        lots_outside_the_window=skipped,
    )


def still_worth_reading(
    listing: Listing,
    now: datetime,
    within_hours: int | None,
) -> bool:
    """Whether the lot can still be bid on, and soon enough to be worth reading.

    A closed lot is not a bargain, it is history, so it never reaches scoring
    however well it would have scored. Beyond that the window is the reader's
    choice: two scheduled runs a day with a window each is how one long list
    becomes an evening digest and a morning one, without the tool needing to
    know anything about schedules.

    A lot that states no closing time is kept. Silence is not a reason to hide
    something the operator asked for.
    """
    if listing.ends_at is None:
        return True
    if listing.ends_at <= now:
        return False
    if within_hours is None:
        return True
    return listing.ends_at <= now + timedelta(hours=within_hours)


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
