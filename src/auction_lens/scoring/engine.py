"""Turning one observed listing into zero or more reportable candidates.

The order here is the policy: a listing that fails a gate is never scored, and
gates come before scores so that a rejection is cheap and obvious to explain.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import AppConfig
from ..logistics import INFEASIBLE, assess_logistics
from ..models import Candidate, Listing, LogisticsDecision, ObservationChange
from .anomaly import score_retail_anomaly
from .conditions import penalty_for
from .context import ScoringContext
from .cost import estimate_total_cost
from .interests import score_interests
from .signals import change_bonus, ending_soon_bonus

FIRST_OBSERVATION = ObservationChange(is_new=True, price_changed=False)


def evaluate(
    listing: Listing,
    config: AppConfig,
    change: ObservationChange | None = None,
    *,
    logistics_decision: LogisticsDecision | None = None,
    now: datetime | None = None,
) -> list[Candidate]:
    """Score one listing against every configured interest and against anomaly."""
    context = build_context(
        listing,
        config,
        change or FIRST_OBSERVATION,
        logistics_decision=logistics_decision,
        now=now or datetime.now(timezone.utc),
    )
    if context is None:
        return []

    candidates = score_interests(context, config.interests, config.scoring.minimum_report_score)
    anomaly = score_retail_anomaly(context, config.scoring)
    if anomaly is not None:
        candidates.append(anomaly)
    return candidates


def build_context(
    listing: Listing,
    config: AppConfig,
    change: ObservationChange,
    *,
    logistics_decision: LogisticsDecision | None,
    now: datetime,
) -> ScoringContext | None:
    """Apply the gates every rule shares, or return None if the listing fails one."""
    if not _is_allowed_location(listing, config.allowed_locations):
        return None
    logistics = assess_logistics(listing, config.logistics, logistics_decision)
    if logistics.status == INFEASIBLE:
        return None
    conditions = frozenset(listing.conditions)
    if conditions & config.scoring.rejected_conditions:
        return None

    return ScoringContext(
        listing=listing,
        conditions=conditions,
        total_cost=estimate_total_cost(listing, config.economics) + logistics.added_cost,
        change=change,
        logistics=logistics,
        baseline_penalty=penalty_for(conditions, config.scoring.condition_penalties),
        ending_soon_bonus=ending_soon_bonus(listing, config.scoring.ending_soon_minutes, now),
        change_bonus=change_bonus(change),
    )


def _is_allowed_location(listing: Listing, allowed_locations: tuple[str, ...]) -> bool:
    """An empty allow-list means every pickup location is acceptable."""
    if not allowed_locations:
        return True
    location = listing.location.lower()
    return any(allowed in location for allowed in allowed_locations)
