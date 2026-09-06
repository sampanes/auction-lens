"""Turning one observed listing into zero or more reportable candidates.

The order here is the policy: a listing that fails a gate is never scored, and
gates come before scores so that a rejection is cheap and obvious to explain.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..config import AppConfig
from ..logistics import assess_logistics
from ..models import (
    Candidate,
    Listing,
    LogisticsDecision,
    LogisticsStatus,
    ObservationChange,
)
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
        now=now or datetime.now(UTC),
    )
    if context is None:
        return []

    candidates = score_interests(context, config.interests, config.scoring.minimum_report_score)
    anomaly = score_retail_anomaly(context, config.scoring)
    if anomaly is not None:
        candidates.append(anomaly)
    return _worth_collecting(candidates, config)


def build_context(
    listing: Listing,
    config: AppConfig,
    change: ObservationChange,
    *,
    logistics_decision: LogisticsDecision | None,
    now: datetime,
) -> ScoringContext | None:
    """Apply the gates every rule shares, or return None if the listing fails one."""
    if not config.locations.permits(listing.location):
        return None
    logistics = assess_logistics(listing, config.logistics, logistics_decision)
    if logistics.status == LogisticsStatus.INFEASIBLE:
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


def _worth_collecting(candidates: list[Candidate], config: AppConfig) -> list[Candidate]:
    """Drop a lot at a far branch unless it is good enough to justify the drive.

    This runs after scoring rather than as a gate, because "good enough" is a
    score, and a gate by definition has not seen one yet.
    """
    return [
        candidate
        for candidate in candidates
        if config.locations.worth_collecting(candidate.listing.location, candidate.score)
    ]
