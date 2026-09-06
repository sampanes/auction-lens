"""One function per command, each doing only what its name says."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from ..acquisition import fetch_authorized_page
from ..config import AppConfig, load_config
from ..fields import parse_money
from ..file_io import write_json_atomically
from ..ingest import load_listings, read_product_page
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

PAGE_SUFFIX = ".html"
LISTINGS_KEY = "listings"

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


def pull(args: argparse.Namespace) -> int:
    """Read saved provider pages into the canonical file that `run` analyses."""
    config = load_config(args.config)
    pages = _saved_pages(Path(args.input))
    rows, failures = [], []
    for page in pages:
        try:
            rows.append(
                read_product_page(
                    page.read_text(encoding="utf-8", errors="replace"),
                    source=config.provider.provider_id,
                )
            )
        except ValueError as error:
            # One page the provider changed must not lose the other fifty.
            failures.append(f"{page.name}: {error}")

    write_json_atomically(Path(args.output), {LISTINGS_KEY: rows})
    print(f"Read {len(rows)} of {len(pages)} saved page(s) into {args.output}.")
    for failure in failures:
        print(f"  [!] {failure}")
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
    if args.verdict == DROP:
        removed = store.drop(args.source, args.listing_id)
        print("Stopped following." if removed else "That lot was not being followed.")
        return SUCCESS

    followed = store.get(args.source, args.listing_id) or WatchedItem(
        source=args.source, listing_id=args.listing_id
    )
    updated = replace(followed, **_stated_opinions(args))
    store.save(updated)
    print(f"{updated.uid}: {updated.verdict}.")
    return SUCCESS


def watchlist(args: argparse.Namespace) -> int:
    """Show the followed lots, keenest first."""
    items = WatchlistStore(Path(args.watchlist)).items()
    if args.verdict:
        items = tuple(item for item in items if item.verdict == args.verdict)
    # Colour only when a person is watching; a redirected list stays plain.
    print(
        render_watchlist(items, path=args.watchlist, colour=sys.stdout.isatty()),
        end="",
    )
    return SUCCESS


def _stated_opinions(args: argparse.Namespace) -> dict:
    """Change only the fields the person actually named on the command line.

    An unnamed field keeps whatever the file already said, so adding one star
    never silently erases the estimate written last week.
    """
    changes = {}
    if args.verdict is not None:
        changes["verdict"] = args.verdict
    if args.estimate is not None:
        changes["my_estimate"] = parse_money(args.estimate, field_name="estimate")
    if args.note is not None:
        changes["note"] = args.note.strip()
    return changes


def _saved_pages(source: Path) -> list[Path]:
    """Accept one page or a directory of them, so a batch is not a special case."""
    if source.is_dir():
        return sorted(source.glob(f"*{PAGE_SUFFIX}"))
    if not source.is_file():
        raise ValueError(f"{source} is not a saved page or a directory of them")
    return [source]


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
