"""Answer what a product has really closed at here, without a sweep running.

The whole of this command is picking the valuation source the operator already
configured and printing what it holds. Deciding what a close is, which window
counts, and when a settled sale beats a reading all belong to
`pricing/closing_history`, which a run reads through as well -- so a range
printed here is the range a card would quote for the same product.
"""

from __future__ import annotations

import argparse

from ..config.load import load_config
from ..pricing.closing_history import (
    ClosingHistoryAdapter,
    recorded_products,
)
from .exit_codes import OPERATOR_ERROR, SUCCESS

# A term matching half the catalogue is a question nobody asked, and printing
# it would bury the answer. The cap is on what is shown, not on what matched,
# and the count of the rest is reported so the reader knows to narrow it.
SHOWN_PRODUCTS = 40


def comps(args: argparse.Namespace) -> int:
    """Print what each matching product has closed at, best evidenced first."""
    config = load_config(args.config)
    source = _history_source(config)
    if source is None:
        print(
            "[X] no closing-history valuation source is configured, so there is "
            "no record to read; add one under [[valuation.sources]]"
        )
        return OPERATOR_ERROR

    records = [
        record
        for record in recorded_products(ClosingHistoryAdapter(source), args.match or "")
        if len(record.prices) >= args.minimum_closes
    ]
    if not records:
        print(_nothing_found(args))
        return SUCCESS

    print(
        f"{len(records)} product(s) with at least {args.minimum_closes} recorded "
        f"close(s){_about(args)}, best evidenced first."
    )
    print(
        "A settled price is what the lot sold for. An observed one is the last "
        "bid seen before the hammer, so it is a floor."
    )
    for record in records[:SHOWN_PRODUCTS]:
        kind = "settled" if record.settled else "observed"
        print()
        print(f"  {record.title}")
        print(
            f"    {len(record.prices)} {kind} close(s): "
            f"${record.low} to ${record.high}, typically ${record.typical}"
        )
    remaining = len(records) - SHOWN_PRODUCTS
    if remaining > 0:
        print(f"\n{remaining} more matched; narrow --match to see them.")
    return SUCCESS


def _history_source(config):
    """The configured closing-history source, or None when there is not one.

    Found by the adapter it names rather than by its id, because the id is the
    operator's to choose and several could be configured.
    """
    for source in config.valuation.sources:
        if source.adapter == "closing_history" and source.enabled:
            return source
    return None


def _about(args: argparse.Namespace) -> str:
    return f" matching {args.match!r}" if args.match else ""


def _nothing_found(args: argparse.Namespace) -> str:
    """Say which of the two reasons it was, because they need different fixes."""
    if args.match:
        return (
            f"Nothing matching {args.match!r} has closed here with at least "
            f"{args.minimum_closes} recorded close(s). A product needs to have "
            "run while this tool was recording, and to have been read near its "
            "close; try a shorter phrase or --minimum-closes 1."
        )
    return (
        "No product has a recorded close yet. The record builds as sweeps run "
        "and lots finish."
    )
