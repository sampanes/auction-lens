"""Turning one observed listing into zero or more reportable candidates.

The order here is the policy: a listing that fails a gate is never scored, and
gates come before scores so that a rejection is cheap and obvious to explain.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..config.app import AppConfig
from ..config.interests import ScoringConfig
from ..config.pricing import EconomicsConfig
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

    candidates = score_interests(context, config.interests, config.scoring)
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
    return (total + _income_tax(listing, economics)).quantize(CENTS)


def total_cost_at_bid(
    candidate: Candidate, bid: Decimal, economics: EconomicsConfig
) -> Decimal:
    """The candidate's total if the bid ends at `bid`, every other cost held.

    Moved from the candidate's own total rather than rebuilt, so a handling
    cost the operator added to this lot is carried along without this
    function having to know it exists.
    """
    moved = replace(candidate.listing, current_bid=bid)
    return (
        candidate.total_cost
        + estimate_total_cost(moved, economics)
        - estimate_total_cost(candidate.listing, economics)
    )


def _income_tax(listing: Listing, economics: EconomicsConfig) -> Decimal:
    """What a lot received rather than bought still costs, which is the tax on it.

    A lot nobody bid on is not free, and reporting its cost as zero states the
    one thing about it that is certainly untrue -- after which every ranking
    built on cost puts it first, and a cost ceiling cannot decline it. The
    declared value is the figure whoever gave the lot reports as income, so the
    rate applied to it is the operator's own rather than anything a provider
    charges. A lot that declares no value is left alone: the cost of an unknown
    is not zero either, but inventing a figure for it would be worse than
    saying so, and the value floors on individual rules are what decline a lot
    nothing is known about.
    """
    if not listing.value_is_taxable_income or listing.estimated_retail is None:
        return NO_PREMIUM
    return listing.estimated_retail * economics.income_tax_rate


def minutes_remaining(listing: Listing, now: datetime) -> float | None:
    """How long this lot has left, or None when it never said."""
    if listing.ends_at is None:
        return None
    return (listing.ends_at - now).total_seconds() / SECONDS_PER_MINUTE


def days_until_close(listing: Listing, now: datetime, zone: ZoneInfo) -> int | None:
    """How many local days after this reading the lot closes, 0 being today.

    Read in the provider's own zone, because "tomorrow" is a claim about the
    calendar a reader lives in and not about UTC. Negative for a lot that has
    already closed, which is a different fact with a different owner.
    """
    if listing.ends_at is None:
        return None
    closing_day = listing.ends_at.astimezone(zone).date()
    reading_day = now.astimezone(zone).date()
    return (closing_day - reading_day).days


def ending_soon_bonus(listing: Listing, within_minutes: int, now: datetime) -> int:
    """Reward an actionable closing listing, but never one already closed."""
    remaining = minutes_remaining(listing, now)
    if remaining is None:
        return 0
    return ENDING_SOON_BONUS if 0 <= remaining <= within_minutes else 0


def score_retail_anomaly(
    context: ScoringContext, scoring: ScoringConfig
) -> Candidate | None:
    """Match an unusually cheap listing even when no interest named it."""
    if not policy_admits(context.conditions, scoring.anomaly_condition):
        return None
    if context.listing.value_is_taxable_income:
        # Nothing was bid, so the cost is the tax and the ratio is the rate --
        # the same number for every such lot, whatever the lot is. That is not
        # a discount anybody offered, and a path that scores it admits an
        # entire catalogue at one identical score. A want still matches these
        # lots on their own words; only the bargain claim is withheld.
        return None
    if not scoring.anomaly_price_is_tested(context.minutes_remaining):
        # The same fault with the clock in place of the tax rate: hours before
        # a lot closes its bid is the opening price, so the ratio is near zero
        # for everything alike and the discount score is near full marks for
        # everything alike. Withheld until the price has been tested, by which
        # time it is also something the reader can still act on.
        return None
    retail = context.listing.estimated_retail
    if retail is None or retail <= 0:
        return None
    ceiling = scoring.anomaly_ceiling(retail)
    if ceiling is None:
        return None
    ratio = context.total_cost / retail
    if ratio > ceiling:
        return None

    penalty = context.baseline_penalty + penalty_for(
        context.conditions, scoring.anomaly_condition.penalties
    )
    score = clamp_score(_discount_score(ratio) + context.score_bonus - penalty)
    if score < _report_floor(scoring, retail, ceiling):
        return None
    reasons = [f"total now is {ratio:.1%} of stated retail"]
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


def _report_floor(scoring: ScoringConfig, retail: Decimal, ceiling: Decimal) -> int:
    """The score an unasked lot must reach, which is the configured floor.

    It is lowered in exactly one case. This score is the discount itself, so a
    ceiling implies the lowest score any lot admitted under it can reach: a
    large-lot ceiling of 40% of retail scores 60, and a floor of 70 would
    refuse every lot the band just admitted, turning a configured band off
    without saying anything. Only the large band can be generous enough for
    that to happen, so only the large band is given the lower number.

    The ordinary band is left alone even when its own ceiling implies a score
    below the floor. A floor set above what a lot can score is a deliberate
    choice -- it says a bare discount is not enough and something else has to
    argue for the lot -- and it is not this function's place to overrule it.
    """
    if not scoring.is_large_lot(retail):
        return scoring.minimum_report_score
    return min(scoring.minimum_report_score, _discount_score(ceiling))


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
        minutes_remaining=minutes_remaining(listing, now),
        days_until_close=days_until_close(listing, now, config.acquisition.zone),
    )


def _worth_collecting(candidates: list[Candidate], config: AppConfig) -> list[Candidate]:
    """Drop a lot at a far branch unless it is good enough to justify the drive.

    This runs after scoring rather than as a gate, because "good enough" is a
    score, and a gate by definition has not seen one yet.
    """
    return [
        candidate
        for candidate in candidates
        if config.locations.worth_collecting(
            candidate.listing.location,
            candidate.score,
            candidate.listing.estimated_retail,
        )
    ]
