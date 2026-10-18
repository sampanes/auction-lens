"""Read what last night's closed lots actually sold for.

This is the one command that asks the provider for a page per lot, so it says
plainly what it is about to do and refuses rather than guessing. The gate, the
pacing and the bounds all live in config; what lives here is the order of
operations and what the operator is told afterwards.

Deliberately not part of `daily`. A settling pass can only run after the last
lot has closed, by which time there is nothing left to report and nobody to
mail, and folding a long run of requests into the workflow that sends mail
would mean a failure in either one took the other down with it.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from ..config.load import load_config
from ..history.database import Database
from ..history.settlements import SettlementStore
from ..providers.registry import resolve_provider
from .exit_codes import SUCCESS

# Far enough past a close that bidding cannot still be extending it. Lots
# extend in thirty-second steps while bids keep arriving, so a lot read a
# minute after its scheduled end may genuinely still be running.
SETTLE_AFTER_MINUTES = 15


def settle(args: argparse.Namespace) -> int:
    """Ask each closed lot's own page what it sold for, and record the answers."""
    config = load_config(args.config)
    adapter = resolve_provider(config.provider.provider_id)
    if not adapter.settles:
        raise RuntimeError(
            f"{config.provider.provider_id} does not publish what a closed lot "
            "sold for, so there is nothing for this command to read"
        )

    database = Database.at(args.database)
    database.initialize()
    store = SettlementStore(database)

    now = datetime.now(UTC)
    window = _window(now, args)
    owed = store.pending_count(**window)
    lots = store.pending(**window, limit=args.limit)
    print(
        f"{owed} closed lot(s) owed a reading in the last {args.since_hours} hour(s); "
        f"this run will read {len(lots)}."
    )
    if not lots:
        return SUCCESS

    outcome = adapter.settle_lots(config.provider, config.acquisition, lots)
    recorded = store.record(list(outcome.settlements), settled_at=now)
    _report(outcome, recorded)
    return SUCCESS


def _window(now: datetime, args: argparse.Namespace) -> dict[str, datetime]:
    """The span of closes this run will ask about, at both ends.

    The near end protects the answer: a lot still extending would report a live
    bid as a sale. The far end protects the provider: without it, a first run
    would walk every lot ever recorded, one request each.
    """
    return {
        "closed_before": now - timedelta(minutes=SETTLE_AFTER_MINUTES),
        "closed_after": now - timedelta(hours=args.since_hours),
    }


def _report(outcome, recorded: int) -> None:
    """Say what came back, counting each kind of silence separately."""
    print(f"Recorded {recorded} settled close(s) from {outcome.requested} page(s) read.")
    extended = sum(1 for item in outcome.settlements if item.extended)
    if extended:
        print(
            f"{extended} of them ran past the close they were scheduled for, "
            "which no earlier pass could have known."
        )
    if outcome.still_open:
        print(
            f"{outcome.still_open} lot(s) were still open when asked and were left "
            "alone; the next run reads them."
        )
    if outcome.missing:
        print(f"{outcome.missing} lot(s) no longer have a page to read.")
    if outcome.unreadable:
        print(f"{outcome.unreadable} page(s) could not be read; their lots stay owed.")
