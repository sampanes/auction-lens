"""The bonuses shared by every scoring path, and the range scores live in.

These numbers are the project's opinion about what matters, so they are named
constants rather than literals buried in an expression.
"""

from __future__ import annotations

from datetime import datetime

from ..models import HIGHEST_SCORE, LOWEST_SCORE, Listing

# A lot about to close is actionable now, which is worth more than a better lot
# that cannot be acted on for another day.
ENDING_SOON_BONUS = 7

SECONDS_PER_MINUTE = 60


def clamp_score(value: int) -> int:
    """Keep every scoring path on the same 0-100 scale."""
    return max(LOWEST_SCORE, min(HIGHEST_SCORE, value))


def ending_soon_bonus(listing: Listing, within_minutes: int, now: datetime) -> int:
    """Reward a listing that closes soon, but not one that has already closed."""
    if listing.ends_at is None:
        return 0
    minutes_remaining = (listing.ends_at - now).total_seconds() / SECONDS_PER_MINUTE
    return ENDING_SOON_BONUS if 0 <= minutes_remaining <= within_minutes else 0
