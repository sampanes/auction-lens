"""The bonuses shared by every scoring path, and the range scores live in.

The numbers themselves live in ``models`` beside the scale they are part of,
because configuration has to be explained in terms of them and cannot import
scoring. What lives here is only how they are applied.
"""

from __future__ import annotations

from datetime import datetime

from ..models import ENDING_SOON_BONUS, HIGHEST_SCORE, LOWEST_SCORE, Listing

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
