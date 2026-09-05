from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .config import load_config
from .ingest import load_listings
from .http_source import fetch_authorized_page
from .models import LogisticsDecision, money
from .reporting import load_env_file, render_text, send_email
from .scoring import evaluate
from .storage import ObservationStore
from .valuation import ValuationEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auction-lens")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="ingest listings, evaluate them, and render a report")
    run.add_argument("--input", required=True, help="canonical .json or .csv listing file")
    run.add_argument("--config", required=True, help="TOML provider and scoring configuration")
    run.add_argument("--database", default="data/auction-lens.sqlite3")
    run.add_argument("--env-file", default=".env", help="optional local KEY=VALUE settings file")
    run.add_argument("--email", action="store_true", help="send the report using configured SMTP settings")
    fetch = subparsers.add_parser("fetch", help="fetch one authorized public provider page")
    fetch.add_argument("--config", required=True, help="TOML provider configuration")
    fetch.add_argument("--env-file", default=".env", help="optional local KEY=VALUE settings file")
    logistics = subparsers.add_parser(
        "logistics", help="save or clear a handling decision for one listing"
    )
    logistics.add_argument("--database", default="data/auction-lens.sqlite3")
    logistics.add_argument("--source", required=True)
    logistics.add_argument("--listing-id", required=True)
    logistics.add_argument(
        "--status", required=True, choices=("feasible", "infeasible", "clear")
    )
    logistics.add_argument("--added-cost", default="0")
    logistics.add_argument("--note", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "logistics":
        store = ObservationStore(Path(args.database))
        store.initialize()
        if args.status == "clear":
            store.clear_logistics_decision(args.source, args.listing_id)
            print("Logistics decision cleared.")
        else:
            decision = LogisticsDecision(
                status=args.status,
                added_cost=money(args.added_cost, field_name="added_cost"),
                note=args.note.strip(),
            )
            store.set_logistics_decision(args.source, args.listing_id, decision)
            print(
                f"Logistics decision saved as {decision.status} "
                f"with ${decision.added_cost} added cost."
            )
        return 0
    load_env_file(args.env_file)
    config = load_config(args.config)
    if args.command == "fetch":
        result = fetch_authorized_page(config.provider)
        cache_state = "cached response reused" if result.reused_cache else f"{result.bytes_received} bytes cached"
        print(f"Provider returned HTTP {result.status}; {cache_state} at {result.cache_path}")
        return 0
    listings = load_listings(args.input)
    store = ObservationStore(Path(args.database))
    store.initialize()
    valuation_engine = ValuationEngine(config.valuation) if config.valuation.enabled else None
    candidates = []
    for listing in listings:
        if listing.source != config.provider.provider_id:
            continue
        change = store.observe(listing)
        logistics_decision = store.get_logistics_decision(
            listing.source, listing.listing_id
        )
        matches = evaluate(
            listing,
            config,
            change,
            logistics_decision=logistics_decision,
        )
        if valuation_engine and matches:
            valuation = valuation_engine.value(listing)
            matches = [replace(candidate, valuation=valuation) for candidate in matches]
        candidates.extend(matches)
    print(render_text(candidates), end="")
    if args.email:
        if not config.email.enabled:
            raise RuntimeError("email reporting is disabled in the selected configuration")
        send_email(candidates, config.email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
