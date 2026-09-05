"""How a listing's condition labels admit or penalize it.

The same two questions are asked by every rule and by anomaly discovery, but
each asks them against its own policy: "not functional" disqualifies a soundbar
meant for use and is irrelevant to the same lot bought for parts.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from ..config import ConditionPolicy


def policy_admits(conditions: Iterable[str], policy: ConditionPolicy) -> bool:
    """Report whether these condition labels are acceptable under one policy."""
    labels = frozenset(conditions)
    if labels & policy.reject:
        return False
    return bool(labels) or policy.allow_unknown


def penalty_for(conditions: Iterable[str], penalties: Mapping[str, int]) -> int:
    """Sum the score penalties that apply to these condition labels."""
    return sum(penalties.get(label, 0) for label in conditions)
