from __future__ import annotations

from .config import LogisticsConfig
from .models import Listing, LogisticsAssessment, LogisticsDecision


def assess_logistics(
    listing: Listing,
    config: LogisticsConfig,
    decision: LogisticsDecision | None = None,
) -> LogisticsAssessment:
    """Identify only the unresolved handling stages; do not model a person's life."""
    if decision is not None:
        if decision.status not in {"feasible", "infeasible"}:
            raise ValueError(f"unknown logistics decision {decision.status!r}")
        return LogisticsAssessment(
            status=decision.status,
            added_cost=decision.added_cost,
            decision_note=decision.note,
        )

    heavy = (
        listing.handling_weight_lb is not None
        and listing.handling_weight_lb > config.manual_handling_limit_lb
    )
    oversized = any(
        dimension > config.large_dimension_threshold_in
        for dimension in listing.package_dimensions_in
    )
    if not heavy and not oversized:
        return LogisticsAssessment(status="ordinary")
    if config.large_item_policy == "reject":
        return LogisticsAssessment(status="infeasible")
    if config.large_item_policy == "allow":
        return LogisticsAssessment(status="assumed_feasible")
    if config.large_item_policy != "ask":
        raise ValueError("large_item_policy must be ask, allow, or reject")

    questions: list[str] = []
    if oversized:
        dimensions = " x ".join(str(value) for value in listing.package_dimensions_in)
        questions.append(f"Confirm the {dimensions} in item fits the planned transport.")
    if heavy:
        weight = listing.handling_weight_lb
        if listing.loading_assistance:
            assistance = ", ".join(listing.loading_assistance)
            questions.append(
                f"Seller loading assistance is listed ({assistance}); confirm the destination unloading plan for {weight} lb."
            )
        else:
            questions.append(
                f"Confirm origin loading and destination unloading plans for {weight} lb."
            )
    return LogisticsAssessment(status="needs_plan", questions=tuple(questions))
