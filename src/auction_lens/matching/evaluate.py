"""Turning one observed listing into zero or more reportable candidates.

The order here is the policy: a listing that fails a gate is never scored, and
gates come before scores so that a rejection is cheap and obvious to explain.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ..config.schema import AppConfig, EconomicsConfig, ScoringConfig
from ..listings.model import Listing, ObservationChange
from ..values import CENTS
from .interests import (
    ScoringContext,
    clamp_score,
    penalty_for,
    policy_admits,
    score_interests,
)
from .logistics import (
    LogisticsDecision,
    LogisticsStatus,
    assess_logistics,
)
from .model import (
    ENDING_SOON_BONUS,
    HIGHEST_SCORE,
    Candidate,
    CandidateCategory,
)

FIRST_OBSERVATION = ObservationChange(is_new=True, price_changed=False)
NO_PREMIUM = Decimal("0")
SECONDS_PER_MINUTE = 60
ANOMALY_RULE_NAME = "retail-ratio"


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


def _costs_too_much_of_retail(
    listing: Listing, total_cost: Decimal, ceiling: Decimal | None
) -> bool:
    """Whether the lot asks more of its stated retail than is ever worth paying.

    A gate rather than a penalty, and deliberately so: no score a want can
    reach should be able to argue for paying near retail on a used, unwarranted
    lot. Placed with the other shared gates so it applies to a wanted match and
    a bargain alike, and so one answer settles it for every rule at once.

    A lot with no stated retail states no ratio, so there is nothing here to
    exceed and the bar does not apply. The value floors on individual rules are
    what decline an unproven claim; this one only judges a claim that was made.
    """
    if ceiling is None or listing.estimated_retail is None:
        return False
    return total_cost > ceiling * listing.estimated_retail


def estimate_total_cost(listing: Listing, economics: EconomicsConfig) -> Decimal:
    """Add the buyer premium, tax, and flat fee to the current bid."""
    premium_rate = listing.buyer_premium_rate
    if premium_rate is None:
        premium_rate = economics.default_buyer_premium
    premium = listing.current_bid * premium_rate
    taxable = listing.current_bid + (
        premium if economics.premium_is_taxable else NO_PREMIUM
    )
    tax = taxable * economics.sales_tax_rate
    total = listing.current_bid + premium + tax + economics.processing_fee
    return total.quantize(CENTS)


def ending_soon_bonus(listing: Listing, within_minutes: int, now: datetime) -> int:
    """Reward an actionable closing listing, but never one already closed."""
    if listing.ends_at is None:
        return 0
    minutes_remaining = (listing.ends_at - now).total_seconds() / SECONDS_PER_MINUTE
    return ENDING_SOON_BONUS if 0 <= minutes_remaining <= within_minutes else 0


def score_retail_anomaly(
    context: ScoringContext, scoring: ScoringConfig
) -> Candidate | None:
    """Match an unusually cheap listing even when no interest named it."""
    if not policy_admits(context.conditions, scoring.anomaly_condition):
        return None
    retail = context.listing.estimated_retail
    if retail is None or retail <= 0 or retail < scoring.anomaly_minimum_retail:
        return None
    ratio = context.total_cost / retail
    if ratio > scoring.anomaly_maximum_ratio:
        return None

    penalty = context.baseline_penalty + penalty_for(
        context.conditions, scoring.anomaly_condition.penalties
    )
    score = clamp_score(_discount_score(ratio) + context.score_bonus - penalty)
    if score < scoring.minimum_report_score:
        return None
    reasons = [f"estimated total is {ratio:.1%} of stated retail"]
    if context.is_ending_soon:
        reasons.append("ending soon")
    return context.candidate(
        category=CandidateCategory.ANOMALY,
        rule_id=ANOMALY_RULE_NAME,
        rule_name=ANOMALY_RULE_NAME,
        score=score,
        reasons=reasons,
        weight=scoring.anomaly_weight,
    )


def _discount_score(ratio: Decimal) -> int:
    return int((Decimal("1") - ratio) * HIGHEST_SCORE)


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
    total_cost = estimate_total_cost(listing, config.economics) + logistics.added_cost
    if _costs_too_much_of_retail(listing, total_cost, config.scoring.maximum_retail_ratio):
        return None

    return ScoringContext(
        listing=listing,
        conditions=conditions,
        total_cost=total_cost,
        change=change,
        logistics=logistics,
        baseline_penalty=penalty_for(conditions, config.scoring.condition_penalties),
        ending_soon_bonus=ending_soon_bonus(listing, config.scoring.ending_soon_minutes, now),
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
