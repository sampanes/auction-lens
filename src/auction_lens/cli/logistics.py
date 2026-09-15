"""Record whether and at what added cost one lot can be collected."""

from __future__ import annotations

import argparse

from ..history.database import Database
from ..history.logistics import LogisticsDecisionStore
from ..matching.logistics import LogisticsDecision, LogisticsStatus
from ..values import parse_money
from .exit_codes import SUCCESS
from .lot_key import lot_identity
from .parser import CLEAR


def logistics(args: argparse.Namespace) -> int:
    """Save or clear one listing's handling decision."""
    source, listing_id = lot_identity(args)
    database = Database.at(args.database)
    database.initialize()
    decisions = LogisticsDecisionStore(database)

    if args.status == CLEAR:
        decisions.clear(source, listing_id)
        print("Logistics decision cleared.")
        return SUCCESS

    decision = LogisticsDecision(
        status=LogisticsStatus(args.status),
        added_cost=parse_money(args.added_cost, field_name="added_cost"),
        note=args.note.strip(),
    )
    decisions.save(source, listing_id, decision)
    print(
        f"Logistics decision saved as {decision.status} "
        f"with ${decision.added_cost} added cost."
    )
    return SUCCESS
