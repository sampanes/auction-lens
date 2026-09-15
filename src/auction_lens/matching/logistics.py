"""Whether one lot can actually be got home, and what the operator said about it.

Separate from scoring on purpose: how good a deal is and whether it fits in the
car are different questions, and only the second one has an answer a person can
give once and have remembered.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..listings.model import Listing
from ..matching.text import mentions
from ..values import require_not_negative

if TYPE_CHECKING:
    from ..config.schema import LogisticsConfig

# Dimensions are rendered in the same order and units the provider supplied.
DIMENSION_JOINER = " x "


class LogisticsStatus(StrEnum):
    """How settled the question of getting one item home is."""

    ORDINARY = "ordinary"
    NEEDS_PLAN = "needs_plan"
    ASSUMED_FEASIBLE = "assumed_feasible"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"


# The two an operator may record. The rest are conclusions Auction Lens drew,
# and a person overrides them by answering rather than by restating them.
OPERATOR_DECIDABLE = (LogisticsStatus.FEASIBLE, LogisticsStatus.INFEASIBLE)


@dataclass(frozen=True)
class LogisticsDecision:
    """An operator's saved answer to a handling question for one listing."""

    status: LogisticsStatus
    added_cost: Decimal = Decimal("0")
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _decidable(self.status))
        require_not_negative(self.added_cost, field_name="added_cost")


@dataclass(frozen=True)
class LogisticsAssessment:
    """What handling stages are still unresolved for one listing."""

    status: LogisticsStatus
    questions: tuple[str, ...] = ()
    added_cost: Decimal = Decimal("0")
    decision_note: str = ""


def _decidable(status: Any) -> LogisticsStatus:
    """Accept either the word or the member, and return the member.

    A saved decision arrives as text from SQLite and as an argument from the
    command line, so it is normalized here rather than at each call site. The
    frozen record is written through ``object.__setattr__`` because
    ``__post_init__`` runs after the field has already been assigned.
    """
    for allowed in OPERATOR_DECIDABLE:
        if status == allowed:
            return allowed
    choices = ", ".join(OPERATOR_DECIDABLE)
    raise ValueError(f"logistics decision must be one of: {choices}")


def assess_logistics(
    listing: Listing,
    config: LogisticsConfig,
    decision: LogisticsDecision | None = None,
) -> LogisticsAssessment:
    """Identify handling questions, unless the operator already answered them."""
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
    if config.large_item_policy == "reject":
        return LogisticsAssessment(status=LogisticsStatus.INFEASIBLE)
    if config.large_item_policy == "allow":
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
    """Use provider wording when the listing publishes no measurements."""
    searchable = listing.searchable_text
    return any(mentions(searchable, term) for term in config.oversized_terms)


def _transport_question(listing: Listing) -> str:
    if not listing.package_dimensions_in:
        return "The listing states this is oversized; confirm the planned transport."
    dimensions = DIMENSION_JOINER.join(str(value) for value in listing.package_dimensions_in)
    return f"Confirm the {dimensions} in item fits the planned transport."


def _open_questions(
    listing: Listing, *, heavy: bool, oversized: bool
) -> tuple[str, ...]:
    """Ask only about stages the listing itself has not already answered."""
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
