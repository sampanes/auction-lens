"""The facts every scoring path shares about one listing.

Interest matching and anomaly discovery reach different verdicts from the same
groundwork -- cost, conditions, timing, and handling -- so that groundwork is
computed once and handed to both.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..models import (
    Candidate,
    CandidateCategory,
    Listing,
    LogisticsAssessment,
    ObservationChange,
)


@dataclass(frozen=True)
class ScoringContext:
    listing: Listing
    conditions: frozenset[str]
    total_cost: Decimal
    change: ObservationChange
    logistics: LogisticsAssessment
    baseline_penalty: int
    ending_soon_bonus: int
    change_bonus: int

    @property
    def is_ending_soon(self) -> bool:
        return self.ending_soon_bonus > 0

    @property
    def bonuses(self) -> int:
        return self.ending_soon_bonus + self.change_bonus

    @property
    def retail_ratio(self) -> Decimal | None:
        """The share of stated retail this listing would cost, when retail is known."""
        retail = self.listing.estimated_retail
        return None if not retail else self.total_cost / retail

    def candidate(
        self,
        *,
        category: CandidateCategory,
        rule_name: str,
        score: int,
        reasons: Sequence[str],
    ) -> Candidate:
        """Attach one verdict to the shared facts about this listing."""
        return Candidate(
            listing=self.listing,
            category=category,
            rule_name=rule_name,
            score=score,
            total_cost=self.total_cost,
            retail_ratio=self.retail_ratio,
            reasons=tuple(reasons),
            change=self.change,
            logistics=self.logistics,
        )
