"""Stable handling and travel limits applied to auction lots."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from ..matching.model import HIGHEST_INTEREST_SCORE, HIGHEST_SCORE, LOWEST_SCORE
from ..values import require_not_negative, require_within, settle_choice


class LargeItemPolicy(StrEnum):
    """What to do about a lot too heavy or too bulky to carry casually."""

    ASK = "ask"
    ALLOW = "allow"
    REJECT = "reject"


@dataclass(frozen=True)
class LogisticsConfig:
    """Coarse thresholds that decide when handling becomes a question."""

    large_item_policy: LargeItemPolicy = LargeItemPolicy.ASK
    manual_handling_limit_lb: Decimal = Decimal("75")
    large_dimension_threshold_in: Decimal = Decimal("60")
    oversized_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        settle_choice(self, "large_item_policy", LargeItemPolicy)
        require_not_negative(
            self.manual_handling_limit_lb, field_name="manual_handling_limit_lb"
        )
        require_not_negative(
            self.large_dimension_threshold_in, field_name="large_dimension_threshold_in"
        )


# Two points below the ceiling lets an exceptional, urgent want justify a trip.
FAR_BRANCH_PENALTY_ALLOWANCE = 2
DEFAULT_FAR_MINIMUM_SCORE = HIGHEST_INTEREST_SCORE - FAR_BRANCH_PENALTY_ALLOWANCE


@dataclass(frozen=True)
class LocationPolicy:
    """Which pickup locations are acceptable, and which must earn the drive."""

    allowed: tuple[str, ...] = ()
    far: tuple[str, ...] = ()
    far_minimum_score: int = DEFAULT_FAR_MINIMUM_SCORE
    far_minimum_retail: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        require_within(
            self.far_minimum_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="far_minimum_score",
        )
        require_not_negative(
            self.far_minimum_retail, field_name="far_minimum_retail"
        )

    def permits(self, location: str) -> bool:
        """An empty allow-list means every pickup location is acceptable."""
        return not self.allowed or _mentions(location, self.allowed)

    def is_far(self, location: str) -> bool:
        return _mentions(location, self.far)

    def already_visiting(self, branches: tuple[str, ...]) -> LocationPolicy:
        """Return the same map with today's existing trips no longer far."""
        visiting = tuple(branch.strip().lower() for branch in branches if branch.strip())
        if not visiting:
            return self
        staying_far = tuple(
            name for name in self.far if not any(name in branch for branch in visiting)
        )
        return replace(self, far=staying_far)

    def worth_collecting(
        self, location: str, score: int, retail: Decimal | None
    ) -> bool:
        """Whether this lot justifies going to this branch.

        Two separate questions, because one number could not answer both.

        The score says how good the lot looks. On a wanted match that is a
        real quality bar. On a price anomaly it is not: that score is a share
        of stated retail turned around, so a bar of 85 means "costs under 15%
        of retail", which on a site whose median lot closes at 16% of retail
        is the ordinary outcome rather than a surprise. Raising the bar does
        not rescue it either, because a lot nobody has bid on yet costs almost
        nothing and therefore scores almost 100.

        The retail floor asks what the score cannot: is the thing big enough
        to be worth the drive at all. A far lot has to pass both.

        A lot with no stated retail cannot answer the second question, so it
        fails it whenever a floor is set. That is deliberate -- the floor is
        there to leave small things at a distance, and an unstated size is not
        evidence of a large one.
        """
        if not self.is_far(location):
            return True
        if score < self.far_minimum_score:
            return False
        if not self.far_minimum_retail:
            return True
        return retail is not None and retail >= self.far_minimum_retail


def _mentions(location: str, names: tuple[str, ...]) -> bool:
    """Match on a name appearing in the branch, so 'mesa' finds 'Mesa, AZ'."""
    written = location.lower()
    return any(name in written for name in names)
