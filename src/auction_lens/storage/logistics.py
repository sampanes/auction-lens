"""Remembering the handling answers an operator has already given."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..logistics import DECIDED_STATUSES
from ..models import LogisticsDecision
from .database import Database

_SELECT_DECISION = """
SELECT status, added_cost, note FROM logistics_decisions
WHERE source = ? AND listing_id = ?
"""

_UPSERT_DECISION = """
INSERT INTO logistics_decisions (
    source, listing_id, status, added_cost, note, updated_at
) VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(source, listing_id) DO UPDATE SET
    status=excluded.status,
    added_cost=excluded.added_cost,
    note=excluded.note,
    updated_at=excluded.updated_at
"""

_DELETE_DECISION = """
DELETE FROM logistics_decisions WHERE source = ? AND listing_id = ?
"""


@dataclass(frozen=True)
class LogisticsDecisionStore:
    """Per-listing handling decisions, keyed the way reports quote them."""

    database: Database

    def get(self, source: str, listing_id: str) -> LogisticsDecision | None:
        with self.database.connect() as connection:
            row = connection.execute(_SELECT_DECISION, (source, listing_id)).fetchone()
        if row is None:
            return None
        status, added_cost, note = row
        return LogisticsDecision(status=status, added_cost=Decimal(added_cost), note=note)

    def save(self, source: str, listing_id: str, decision: LogisticsDecision) -> None:
        if decision.status not in DECIDED_STATUSES:
            raise ValueError("logistics decision must be feasible or infeasible")
        with self.database.connect() as connection:
            connection.execute(
                _UPSERT_DECISION,
                (
                    source,
                    listing_id,
                    decision.status,
                    str(decision.added_cost),
                    decision.note,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def clear(self, source: str, listing_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(_DELETE_DECISION, (source, listing_id))
