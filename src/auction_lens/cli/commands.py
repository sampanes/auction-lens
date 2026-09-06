"""One function per command, each doing only what its name says."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from ..acquisition import fetch_authorized_page
from ..config import AppConfig, load_config
from ..fields import parse_money
from ..ingest import load_listings
from ..models import LogisticsDecision, LogisticsStatus, WatchedItem
from ..pipeline import analyze_listings
from ..reporting import render_text, render_watchlist, send_email
from ..storage import (
    Database,
    LogisticsDecisionStore,
    ObservationStore,
    WatchlistStore,
)
from ..valuation import ValuationEngine
from .parser import CLEAR, DROP

SUCCESS = 0


def run(args: argparse.Namespace) -> int:
    """Score a listing file and print, and optionally email, the report."""
    config = load_config(args.config)
    listings = load_listings(args.input)
    database = Database.at(args.database)
    database.initialize()

    result = analyze_listings(
        listings,
        config,
        observations=ObservationStore(database),
        decisions=LogisticsDecisionStore(database),
        watchlist=WatchlistStore(Path(args.watchlist)),
        valuation_engine=_valuation_engine(config),
    )
    print(render_text(result.candidates), end="")
    _report_skipped(result.listings_from_other_providers, config.provider.provider_id)
    _report_followed(result.lots_followed, args.watchlist)

    if args.email:
        if not config.email.enabled:
            raise RuntimeError("email reporting is disabled in the selected configuration")
        send_email(result.candidates, config.email)
    return SUCCESS


def fetch(args: argparse.Namespace) -> int:
    """Fetch one authorized page and report what happened to the cache."""
    config = load_config(args.config)
    result = fetch_authorized_page(config.provider, config.acquisition)
    provider = config.provider.display_name or config.provider.provider_id
    outcome = (
        "cached response reused"
        if result.reused_cache
        else f"{result.bytes_received} bytes cached"
    )
    print(f"{provider} returned HTTP {result.status}; {outcome} at {result.cache_path}")
    return SUCCESS


def logistics(args: argparse.Namespace) -> int:
    """Save or clear one listing's handling decision."""
    database = Database.at(args.database)
    database.initialize()
    decisions = LogisticsDecisionStore(database)

    if args.status == CLEAR:
        decisions.clear(args.source, args.listing_id)
        print("Logistics decision cleared.")
        return SUCCESS

    decision = LogisticsDecision(
        status=LogisticsStatus(args.status),
        added_cost=parse_money(args.added_cost, field_name="added_cost"),
        note=args.note.strip(),
    )
    decisions.save(args.source, args.listing_id, decision)
    print(
        f"Logistics decision saved as {decision.status} "
        f"with ${decision.added_cost} added cost."
    )
    return SUCCESS


def watch(args: argparse.Namespace) -> int:
    """Record what a person thinks of one lot, or stop following it."""
    store = WatchlistStore(Path(args.watchlist))
    if args.state == DROP:
        removed = store.drop(args.source, args.listing_id)
        print("Stopped following." if removed else "That lot was not being followed.")
        return SUCCESS

    followed = store.get(args.source, args.listing_id) or WatchedItem(
        source=args.source, listing_id=args.listing_id
    )
    updated = replace(followed, **_stated_opinions(args))
    store.save(updated)
    print(f"{updated.uid} is {updated.state} [{updated.tag}], {updated.stars} star(s).")
    return SUCCESS


def watchlist(args: argparse.Namespace) -> int:
    """Show the followed lots, keenest first."""
    items = WatchlistStore(Path(args.watchlist)).items()
    if args.state:
        items = tuple(item for item in items if item.state == args.state)
    print(render_watchlist(items, path=args.watchlist), end="")
    return SUCCESS


def _stated_opinions(args: argparse.Namespace) -> dict:
    """Change only the fields the person actually named on the command line.

    An unnamed field keeps whatever the file already said, so adding one star
    never silently erases the estimate written last week.
    """
    changes = {}
    if args.state is not None:
        changes["state"] = args.state
    if args.stars is not None:
        changes["stars"] = args.stars
    if args.estimate is not None:
        changes["my_estimate"] = parse_money(args.estimate, field_name="estimate")
    if args.note is not None:
        changes["note"] = args.note.strip()
    return changes


def _valuation_engine(config: AppConfig) -> ValuationEngine | None:
    """Value listings only when the configuration asked for it."""
    return ValuationEngine(config.valuation) if config.valuation.enabled else None


def _report_skipped(count: int, provider_id: str) -> None:
    """Say so when input was ignored, rather than silently dropping listings."""
    if count:
        print(f"Ignored {count} listing(s) from other providers than {provider_id}.")


def _report_followed(count: int, path: str) -> None:
    """Say where the price readings went, so the file is never a surprise."""
    if count:
        print(f"Recorded a price reading for {count} lot(s) in {path}.")
