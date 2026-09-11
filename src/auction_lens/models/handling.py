"""Whether one lot can actually be got home, and what the operator said about it.

Separate from scoring on purpose: how good a deal is and whether it fits in the
car are different questions, and only the second one has an answer a person can
give once and have remembered.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ..fields import require_not_negative


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
