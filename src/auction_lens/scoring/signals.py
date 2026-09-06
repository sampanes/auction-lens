"""The bonuses shared by every scoring path, and the range scores live in.

These numbers are the project's opinion about what matters, so they are named
constants rather than literals buried in an expression.
"""

from __future__ import annotations

from datetime import datetime

from ..models import HIGHEST_SCORE, LOWEST_SCORE, Listing, ObservationChange

# A listing first seen this run is worth more attention than one already read
# about, and a moved price is worth slightly more than an unchanged one.
NEW_LISTING_BONUS = 3
PRICE_CHANGE_BONUS = 2

# A lot about to close is actionable now, which is worth more than a better lot
# that cannot be acted on for another day.
ENDING_SOON_BONUS = 7

SECONDS_PER_MINUTE = 60


def clamp_score(value: int) -> int:
    """Keep every scoring path on the same 0-100 scale."""
    return max(LOWEST_SCORE, min(HIGHEST_SCORE, value))


def change_bonus(change: ObservationChange) -> int:
    if change.is_new:
        return NEW_LISTING_BONUS
    if change.price_changed:
        return PRICE_CHANGE_BONUS
    return LOWEST_SCORE


def ending_soon_bonus(listing: Listing, within_minutes: int, now: datetime) -> int:
    """Reward a listing that closes soon, but not one that has already closed."""
    if listing.ends_at is None:
        return 0
    minutes_remaining = (listing.ends_at - now).total_seconds() / SECONDS_PER_MINUTE
    return ENDING_SOON_BONUS if 0 <= minutes_remaining <= within_minutes else 0
