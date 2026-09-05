"""Deciding when getting an item home is an open question.

The thresholds here are deliberately coarse. Auction Lens does not model a
person's vehicle, helpers, or doorways; it only notices that a lot is heavy or
bulky enough that someone should think before bidding, and says so once.
"""

from __future__ import annotations

from .config import LogisticsConfig
from .models import Listing, LogisticsAssessment, LogisticsDecision

# Assessment outcomes, in the order a listing moves through them.
ORDINARY = "ordinary"
NEEDS_PLAN = "needs_plan"
ASSUMED_FEASIBLE = "assumed_feasible"
FEASIBLE = "feasible"
INFEASIBLE = "infeasible"

# The two outcomes an operator can record for a specific listing.
DECIDED_STATUSES = frozenset({FEASIBLE, INFEASIBLE})

ASK = "ask"
ALLOW = "allow"
REJECT = "reject"

DIMENSION_SEPARATOR = " x "


def assess_logistics(
    listing: Listing,
    config: LogisticsConfig,
    decision: LogisticsDecision | None = None,
) -> LogisticsAssessment:
    """Identify the handling stages still unresolved for one listing.

    A saved decision always wins: once a person has answered the question for a
    listing, the report should stop asking it.
    """
    if decision is not None:
        return _from_decision(decision)

    heavy = _is_heavy(listing, config)
    oversized = _is_oversized(listing, config)
    if not heavy and not oversized:
        return LogisticsAssessment(status=ORDINARY)
    if config.large_item_policy == REJECT:
        return LogisticsAssessment(status=INFEASIBLE)
    if config.large_item_policy == ALLOW:
        return LogisticsAssessment(status=ASSUMED_FEASIBLE)
    if config.large_item_policy != ASK:
        raise ValueError("large_item_policy must be ask, allow, or reject")
    return LogisticsAssessment(
        status=NEEDS_PLAN,
        questions=_open_questions(listing, heavy=heavy, oversized=oversized),
    )


def _from_decision(decision: LogisticsDecision) -> LogisticsAssessment:
    if decision.status not in DECIDED_STATUSES:
        raise ValueError(f"unknown logistics decision {decision.status!r}")
    return LogisticsAssessment(
        status=decision.status,
        added_cost=decision.added_cost,
        decision_note=decision.note,
    )


def _is_heavy(listing: Listing, config: LogisticsConfig) -> bool:
    weight = listing.handling_weight_lb
    return weight is not None and weight > config.manual_handling_limit_lb


def _is_oversized(listing: Listing, config: LogisticsConfig) -> bool:
    return any(
        dimension > config.large_dimension_threshold_in
        for dimension in listing.package_dimensions_in
    )


def _open_questions(listing: Listing, *, heavy: bool, oversized: bool) -> tuple[str, ...]:
    """Ask only about stages the listing itself has not already answered.

    Seller loading assistance resolves the origin, and nothing else: it says
    nothing about whether the item fits the transport or can be unloaded.
    """
    questions = []
    if oversized:
        dimensions = DIMENSION_SEPARATOR.join(
            str(value) for value in listing.package_dimensions_in
        )
        questions.append(f"Confirm the {dimensions} in item fits the planned transport.")
    if heavy:
        weight = listing.handling_weight_lb
        if listing.loading_assistance:
            assistance = ", ".join(listing.loading_assistance)
            questions.append(
                f"Seller loading assistance is listed ({assistance}); "
                f"confirm the destination unloading plan for {weight} lb."
            )
        else:
            questions.append(
                f"Confirm origin loading and destination unloading plans for {weight} lb."
            )
    return tuple(questions)
