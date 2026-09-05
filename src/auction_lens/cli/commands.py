"""One function per command, each doing only what its name says."""

from __future__ import annotations

import argparse

from ..acquisition import fetch_authorized_page
from ..config import load_config
from ..fields import parse_money
from ..ingest import load_listings
from ..models import LogisticsDecision
from ..pipeline import analyze_listings
from ..reporting import render_text, send_email
from ..storage import Database, LogisticsDecisionStore, ObservationStore
from ..valuation import ValuationEngine
from .parser import CLEAR

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
        valuation_engine=ValuationEngine(config.valuation) if config.valuation.enabled else None,
    )
    print(render_text(result.candidates), end="")
    _report_skipped(result.listings_from_other_providers, config.provider.provider_id)

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
        status=args.status,
        added_cost=parse_money(args.added_cost, field_name="added_cost"),
        note=args.note.strip(),
    )
    decisions.save(args.source, args.listing_id, decision)
    print(
        f"Logistics decision saved as {decision.status} "
        f"with ${decision.added_cost} added cost."
    )
    return SUCCESS


def _report_skipped(count: int, provider_id: str) -> None:
    """Say so when input was ignored, rather than silently dropping listings."""
    if count:
        print(f"Ignored {count} listing(s) from other providers than {provider_id}.")
