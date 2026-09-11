"""Which finite interests are still active, derived from explicit outcomes.

Configuration says how many are wanted. The private watchlist says which won
lots actually fulfilled which wants. Neither owns a stored ``retired`` switch:
joining those two facts here means correcting a verdict, clearing a fulfillment,
or raising a target takes effect on the very next run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .config import InterestRule
from .models import InterestProgress, InterestRef, Verdict, WatchedItem


@dataclass(frozen=True)
class InterestPlan:
    """The rules to score now, plus the human-readable reason some are absent."""

    active_rules: tuple[InterestRule, ...]
    progress: tuple[InterestProgress, ...]
    unreviewed_wins: int = 0


def plan_interests(
    rules: tuple[InterestRule, ...], watched: Iterable[WatchedItem]
) -> InterestPlan:
    """Apply explicit won fulfillments to targets without changing either input."""
    finite_ids = {
        rule.interest_id.casefold() for rule in rules if rule.wanted is not None
    }
    fulfilled, unreviewed = _confirmed_fulfillments(watched, finite_ids)
    progress = tuple(
        InterestProgress(
            interest=InterestRef(rule.interest_id, rule.name),
            wanted=rule.wanted,
            fulfilled=fulfilled[rule.interest_id.casefold()],
        )
        for rule in rules
    )
    active_ids = {
        item.interest.interest_id.casefold()
        for item in progress
        if not item.is_retired
    }
    return InterestPlan(
        active_rules=tuple(
            rule for rule in rules if rule.interest_id.casefold() in active_ids
        ),
        progress=progress,
        unreviewed_wins=unreviewed,
    )


def _confirmed_fulfillments(
    watched: Iterable[WatchedItem], finite_ids: set[str]
) -> tuple[Counter[str], int]:
    """Count each won item once and flag only unreviewed finite matches."""
    counts: Counter[str] = Counter()
    allocations_by_key: dict[str, set[str]] = {}
    matches_by_key: dict[str, set[str]] = {}
    reviewed_by_key: dict[str, bool] = {}
    for item in watched:
        matches_by_key.setdefault(item.item_key, set()).update(
            reference.interest_id.casefold() for reference in item.matched_interests
        )
        reviewed_by_key[item.item_key] = (
            reviewed_by_key.get(item.item_key, False) or item.fulfillment_reviewed
        )
        if item.verdict != Verdict.WON:
            continue
        allocations_by_key.setdefault(item.item_key, set()).update(
            reference.interest_id.casefold()
            for reference in item.fulfilled_interests
        )
    unreviewed = sum(
        not allocations
        and not reviewed_by_key[key]
        and bool(matches_by_key[key] & finite_ids)
        for key, allocations in allocations_by_key.items()
    )
    for allocations in allocations_by_key.values():
        counts.update(allocations)
    return counts, unreviewed
