"""Deciding when getting an item home is an open question.

The thresholds here are deliberately coarse. Auction Lens does not model a
person's vehicle, helpers, or doorways; it only notices that a lot is heavy or
bulky enough that someone should think before bidding, and says so once.

What can be collected is a fact about the operator rather than about the lot,
and it changes: the day a trailer arrives, every oversized lot becomes an
ordinary one. So this asks instead of excluding, and the day it stops being a
question is a single setting -- large_item_policy = "allow" -- rather than a
filter to find and unpick across a dozen interest rules.
"""

from __future__ import annotations

from .config import LargeItemPolicy, LogisticsConfig
from .models import Listing, LogisticsAssessment, LogisticsDecision, LogisticsStatus
from .text_match import mentions

# How dimensions are written back out to a person. Reading them apart is a
# different job, and lives in fields.DIMENSION_SEPARATOR.
DIMENSION_JOINER = " x "


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
        return LogisticsAssessment(
            status=decision.status,
            added_cost=decision.added_cost,
            decision_note=decision.note,
        )

    heavy = _is_heavy(listing, config)
    oversized = _is_oversized(listing, config) or _declares_oversized(listing, config)
    if not heavy and not oversized:
        return LogisticsAssessment(status=LogisticsStatus.ORDINARY)
    if config.large_item_policy == LargeItemPolicy.REJECT:
        return LogisticsAssessment(status=LogisticsStatus.INFEASIBLE)
    if config.large_item_policy == LargeItemPolicy.ALLOW:
        return LogisticsAssessment(status=LogisticsStatus.ASSUMED_FEASIBLE)
    return LogisticsAssessment(
        status=LogisticsStatus.NEEDS_PLAN,
        questions=_open_questions(listing, heavy=heavy, oversized=oversized),
    )


def _is_heavy(listing: Listing, config: LogisticsConfig) -> bool:
    weight = listing.handling_weight_lb
    return weight is not None and weight > config.manual_handling_limit_lb


def _is_oversized(listing: Listing, config: LogisticsConfig) -> bool:
    return any(
        dimension > config.large_dimension_threshold_in
        for dimension in listing.package_dimensions_in
    )


def _declares_oversized(listing: Listing, config: LogisticsConfig) -> bool:
    """Take the provider's word for it when it never published a measurement.

    Thresholds only work on lots that state weight and dimensions, and many
    state neither while announcing the same fact in words: "truck/trailer pickup
    only" is a measurement, written for a human. The phrases are configured
    rather than built in, because each provider says it differently.
    """
    searchable = listing.searchable_text
    return any(mentions(searchable, term) for term in config.oversized_terms)


def _transport_question(listing: Listing) -> str:
    """Quote the measurement where there is one, and the claim where there is not."""
    if not listing.package_dimensions_in:
        return "The listing states this is oversized; confirm the planned transport."
    dimensions = DIMENSION_JOINER.join(str(value) for value in listing.package_dimensions_in)
    return f"Confirm the {dimensions} in item fits the planned transport."


def _open_questions(listing: Listing, *, heavy: bool, oversized: bool) -> tuple[str, ...]:
    """Ask only about stages the listing itself has not already answered.

    Seller loading assistance resolves the origin, and nothing else: it says
    nothing about whether the item fits the transport or can be unloaded.
    """
    questions = []
    if oversized:
        questions.append(_transport_question(listing))
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
