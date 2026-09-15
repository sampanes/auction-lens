"""Which configured want a lot answered, and how that want is getting on.

An interest is named in configuration, but a decision recorded last month has
to stay readable after that want is renamed, so what is stored is the stable id
with the name the person saw at the time.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config.interests import InterestRule
from ..values import require_at_least, require_not_negative

if TYPE_CHECKING:
    from ..watchlist.model import WatchedItem


@dataclass(frozen=True)
class InterestRef:
    """A stable interest identity with the name a person saw at the time.

    The id answers which configured want this was. The name is kept beside it
    because an old decision should remain readable after that want is renamed.
    """

    interest_id: str
    name: str

    def __post_init__(self) -> None:
        interest_id = self.interest_id.strip()
        name = self.name.strip()
        if not interest_id:
            raise ValueError("interest id must be non-empty text")
        if not name:
            raise ValueError("interest name must be non-empty text")
        object.__setattr__(self, "interest_id", interest_id)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True)
class InterestProgress:
    """How a configured want stands against its explicit fulfillments."""

    interest: InterestRef
    wanted: int | None
    fulfilled: int = 0

    def __post_init__(self) -> None:
        if self.wanted is not None:
            require_at_least(self.wanted, 1, field_name="wanted")
        require_not_negative(self.fulfilled, field_name="fulfilled")

    @property
    def is_limited(self) -> bool:
        return self.wanted is not None

    @property
    def is_retired(self) -> bool:
        return self.wanted is not None and self.fulfilled >= self.wanted

    @property
    def remaining(self) -> int | None:
        if self.wanted is None:
            return None
        return max(self.wanted - self.fulfilled, 0)


def _unique_interest_refs(
    references: tuple[InterestRef, ...], *, field_name: str
) -> tuple[InterestRef, ...]:
    """Keep one reference per stable id and reject ambiguous direct callers."""
    unique = []
    seen = set()
    for reference in references:
        if not isinstance(reference, InterestRef):
            raise ValueError(f"{field_name} must contain interest references")
        key = reference.interest_id.casefold()
        if key in seen:
            raise ValueError(f"{field_name} contains duplicate id: {reference.interest_id}")
        seen.add(key)
        unique.append(reference)
    return tuple(unique)


@dataclass(frozen=True)
class InterestPlan:
    """The rules to score now, plus the reason some are already complete."""

    active_rules: tuple[InterestRule, ...]
    progress: tuple[InterestProgress, ...]
    unreviewed_wins: int = 0


def plan_interests(
    rules: tuple[InterestRule, ...], watched: Iterable[WatchedItem]
) -> InterestPlan:
    """Derive active interests from explicit, reversible purchase outcomes."""
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
        if item.verdict != "won":
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
