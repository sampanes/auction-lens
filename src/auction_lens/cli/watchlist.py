"""Record, display, and optionally email the lots a person follows."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from ..config.load import load_config
from ..matching.progress import InterestRef
from ..reports.delivery import (
    DeliveryChannel,
    DeliveryRoute,
    ReportKind,
    outcome_fingerprint,
    plan_watchlist,
    watchlist_items,
)
from ..reports.destinations import destination_fingerprint
from ..reports.email import send_watchlist_email
from ..reports.receipts import DeliveryLedger
from ..reports.records import DeliverySummary
from ..reports.send import delivery_failure, preflight_reports
from ..values import parse_money
from ..watchlist.model import Verdict, WatchedItem
from ..watchlist.report import render_watchlist
from ..watchlist.store import WatchlistStore
from .exit_codes import SUCCESS
from .lot_key import lot_identity
from .parser import DROP


def watch(args: argparse.Namespace) -> int:
    """Record what a person thinks of one lot, or stop following it."""
    source, listing_id = lot_identity(args)
    store = WatchlistStore(Path(args.watchlist))
    if args.verdict == DROP:
        if args.fulfills is not None or args.clear_fulfillments:
            raise ValueError("drop cannot be combined with fulfillment changes")
        followed = store.get(source, listing_id)
        if followed is not None and followed.fulfilled_interests:
            names = ", ".join(
                reference.name for reference in followed.fulfilled_interests
            )
            raise ValueError(
                f"cannot drop {followed.key} while it fulfills: {names}; "
                "first run watch with --clear-fulfillments (this reopens the "
                "interest), then run watch with --verdict drop"
            )
        removed = store.drop(source, listing_id)
        print("Stopped following." if removed else "That lot was not being followed.")
        return SUCCESS

    followed = store.get(source, listing_id) or WatchedItem(
        source=source, listing_id=listing_id
    )
    changes = _stated_opinions(args)
    resulting_verdict = Verdict(changes.get("verdict", followed.verdict))
    if args.fulfills is not None:
        if resulting_verdict != Verdict.WON:
            raise ValueError(
                "--fulfills requires the resulting verdict to be won; "
                "add --verdict won"
            )
        changes["fulfilled_interests"] = _resolve_fulfillments(
            args.fulfills, followed.matched_interests
        )
        changes["fulfillment_reviewed"] = True
    elif args.clear_fulfillments:
        changes["fulfilled_interests"] = ()
        changes["fulfillment_reviewed"] = True

    updated = replace(followed, **changes)
    store.save(updated)
    print(_watch_confirmation(updated))
    return SUCCESS


def _stated_opinions(args: argparse.Namespace) -> dict:
    """Change only fields the person actually named on the command line."""
    changes = {}
    if args.verdict is not None:
        changes["verdict"] = args.verdict
    if args.estimate is not None:
        changes["my_estimate"] = parse_money(args.estimate, field_name="estimate")
    if args.note is not None:
        changes["note"] = args.note.strip()
    return changes


def _resolve_fulfillments(
    requested: list[str], recorded: tuple[InterestRef, ...]
) -> tuple[InterestRef, ...]:
    """Resolve names against the matches recorded with this historical lot.

    Stable ids win over names, so a display-name collision cannot make an id
    unusable. A name must identify exactly one recorded match.
    """
    resolved = []
    for entered in requested:
        key = entered.strip().casefold()
        id_match = next(
            (ref for ref in recorded if ref.interest_id.casefold() == key), None
        )
        if id_match is not None:
            chosen = id_match
        else:
            name_matches = [ref for ref in recorded if ref.name.casefold() == key]
            if len(name_matches) != 1:
                problem = "is ambiguous" if name_matches else "is not a recorded match"
                raise ValueError(
                    f"cannot use {entered!r} for --fulfills: it {problem}. "
                    f"Recorded matches: {_recorded_matches(recorded)}"
                )
            chosen = name_matches[0]
        if all(
            existing.interest_id.casefold() != chosen.interest_id.casefold()
            for existing in resolved
        ):
            resolved.append(chosen)
    return tuple(resolved)


def _recorded_matches(references: tuple[InterestRef, ...]) -> str:
    """Return a copyable list after an unknown or ambiguous fulfillment name."""
    if not references:
        return "none (run this listing through Auction Lens before assigning it)"
    return ", ".join(
        f"{reference.interest_id} ({reference.name})" for reference in references
    )


def _watch_confirmation(item: WatchedItem) -> str:
    """Confirm whether a saved allocation currently counts toward a want."""
    opening = f"{item.key}: {item.verdict}."
    if item.verdict == Verdict.WON:
        if item.fulfilled_interests:
            names = ", ".join(ref.name for ref in item.fulfilled_interests)
            return f"{opening} Fulfills: {names}."
        if item.fulfillment_reviewed:
            return f"{opening} Fulfillment reviewed: fulfills none."
        if item.matched_interests:
            return (
                f"{opening} Fulfillment unreviewed; use --fulfills INTEREST "
                "to assign it, or --clear-fulfillments if it fulfilled none."
            )
        return f"{opening} Fulfillment unreviewed; no matches were recorded."
    if item.fulfilled_interests:
        return f"{opening} Saved fulfillment is inactive until the verdict is won."
    if item.fulfillment_reviewed:
        return f"{opening} Fulfillment reviewed: fulfills none."
    return opening


def watchlist(args: argparse.Namespace) -> int:
    """Show the followed lots, keenest first, and optionally email them."""
    if args.repeat_delivery and not args.email:
        raise ValueError("--repeat-delivery requires --email")
    items = WatchlistStore(Path(args.watchlist)).items()
    if args.verdict:
        items = tuple(item for item in items if item.verdict == args.verdict)
    # Colour only when a person is watching; a redirected list stays plain.
    print(
        render_watchlist(items, path=args.watchlist, colour=sys.stdout.isatty()),
        end="",
    )
    if args.email:
        if not items:
            print("No selected lots; no email sent.")
            return SUCCESS
        config = load_config(args.config)
        destinations = preflight_reports(config, args)
        destination = destinations[DeliveryChannel.EMAIL]
        # A filtered watchlist is a separate recurring report. Combining its
        # public selector with the opaque destination keeps the receipt streams
        # separate without retaining either private destination value.
        selector = "all" if args.verdict is None else str(args.verdict)
        route_fingerprint = destination_fingerprint(
            f"{destination}\0watchlist-selection={selector}"
        )
        route = DeliveryRoute(
            ReportKind.WATCHLIST,
            DeliveryChannel.EMAIL,
            route_fingerprint,
        )
        accepted = False
        phase = "receipt planning"
        try:
            with DeliveryLedger(Path(args.delivery_ledger)).session(route) as delivery:
                proposed = watchlist_items(items)
                plan = plan_watchlist(
                    items,
                    delivery.revisions(proposed),
                    repeat=args.repeat_delivery,
                )
                if not plan.items and not args.repeat_delivery:
                    print(
                        "Watchlist email is up to date; "
                        f"{plan.unchanged_items} unchanged selected lot(s) were "
                        "already delivered."
                    )
                    return SUCCESS
                phase = "transport"
                send_watchlist_email(
                    plan.items,
                    config.email,
                    DeliverySummary(
                        active=True,
                        repeated=args.repeat_delivery,
                        unchanged_matches=plan.unchanged_items,
                        item_singular="selected lot",
                        item_plural="selected lots",
                    ),
                )
                accepted = True
                phase = "local receipt"
                delivery.accept(plan.receipts, outcome_fingerprint((), 0))
            print(f"Emailed {len(plan.items)} selected lot(s).")
        except (OSError, RuntimeError, ValueError) as error:
            raise RuntimeError(
                delivery_failure(
                    DeliveryChannel.EMAIL,
                    accepted,
                    phase,
                    error,
                )
            ) from error
    return SUCCESS
