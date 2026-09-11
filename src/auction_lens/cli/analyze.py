"""The two commands that turn a file of lots into a report someone reads.

``run`` scores a file that already exists. ``daily`` fetches one first and then
does exactly the same thing, which is why the shared half is a function here
rather than a second implementation.

The small printers at the bottom all answer one question: what did this run
leave out? A short report and a quiet day must never look the same.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from ..config import AppConfig, load_config
from ..ingest import load_listings
from ..notifications import DeliveryChannel
from ..outcomes import plan_interests
from ..pipeline import analyze_listings
from ..reporting import render_text
from ..storage import (
    Database,
    LogisticsDecisionStore,
    ObservationStore,
    WatchlistStore,
)
from ..valuation import ValuationEngine
from .collect import run_discovery, write_satisfied_discovery
from .exit_codes import SUCCESS
from .searching import daily_search_terms
from .sending import deliver_findings, preflight_reports


def daily(args: argparse.Namespace) -> int:
    """Find lots, score them, and report what matters: the whole day in one word.

    Discovery and analysis stay separate commands because each is useful alone
    -- a parser can be corrected and re-run without asking the provider again --
    but nobody wants to type both every morning.
    """
    config = _with_todays_trips(load_config(args.config), args.visiting)
    destinations = preflight_reports(config, args)
    watchlist = WatchlistStore(Path(args.watchlist))
    plan = plan_interests(config.interests, watchlist.items())
    terms = daily_search_terms(config, args.search, plan.active_rules)
    if (
        not terms
        and not config.acquisition.categories
        and config.interests
        and not plan.active_rules
    ):
        write_satisfied_discovery(args.output)
    else:
        run_discovery(args, config, terms)
    run_args = argparse.Namespace(**{**vars(args), "input": args.output})
    return _score_and_report(run_args, config, watchlist, destinations)


def run(args: argparse.Namespace) -> int:
    """Score a listing file and print, and optionally email, the report."""
    config = _with_todays_trips(load_config(args.config), args.visiting)
    destinations = preflight_reports(config, args)
    return _score_and_report(args, config, WatchlistStore(Path(args.watchlist)), destinations)


def _score_and_report(
    args: argparse.Namespace,
    config: AppConfig,
    watchlist: WatchlistStore,
    destinations: dict[DeliveryChannel, str],
) -> int:
    """Execute an already-loaded run, so ``daily`` need not load its inputs twice."""
    listings = load_listings(args.input)
    database = Database.at(args.database)
    database.initialize()

    result = analyze_listings(
        listings,
        config,
        observations=ObservationStore(database),
        decisions=LogisticsDecisionStore(database),
        watchlist=watchlist,
        valuation_engine=_valuation_engine(config),
    )
    zone = config.acquisition.zone
    searches = result.searches
    order = config.reports.order
    print(
        render_text(
            result.candidates,
            zone,
            searches,
            order,
            result.interest_progress,
            result.unreviewed_wins,
            harvest=result.harvest,
        ),
        end="",
    )
    _report_skipped(result.listings_from_other_providers, config.provider.provider_id)
    _report_already_closed(result.lots_already_closed)
    _report_capped(result.matches_not_shown, len(result.candidates))
    additionally_followed, delivery_failures = deliver_findings(
        args,
        config,
        result,
        watchlist,
        destinations,
    )
    _report_followed(result.lots_followed + additionally_followed, args.watchlist)
    if delivery_failures:
        raise RuntimeError("; ".join(delivery_failures))
    return SUCCESS


def _with_todays_trips(config: AppConfig, visiting: list[str]) -> AppConfig:
    """Apply the errands already planned, which is a fact about today only.

    Deliberately a flag rather than a setting: it is true for one run and wrong
    by next week, and a saved answer to a question like this is one nobody
    remembers to change back.
    """
    if not visiting:
        return config
    return replace(config, locations=config.locations.already_visiting(tuple(visiting)))


def _valuation_engine(config: AppConfig) -> ValuationEngine | None:
    """Value listings only when the configuration asked for it."""
    return ValuationEngine(config.valuation) if config.valuation.enabled else None


def _report_skipped(count: int, provider_id: str) -> None:
    """Say so when input was ignored, rather than silently dropping listings."""
    if count:
        print(f"Ignored {count} listing(s) from other providers than {provider_id}.")


def _report_already_closed(count: int) -> None:
    """Account for the lots that were read but could no longer be bid on."""
    if count:
        print(f"Passed over {count} lot(s) that have already closed.")


def _report_capped(hidden: int, shown: int) -> None:
    """Never hide part of the ranking quietly.

    A cap the reader has forgotten about looks exactly like a quiet day, and
    the difference between "nothing was out there" and "you asked for less"
    matters enough to spend a line on.
    """
    if hidden:
        print(
            f"Showing the best {shown}; {hidden} more matched. "
            "Raise reports.max_items to see them."
        )


def _report_followed(count: int, path: str) -> None:
    """Say where followed-lot history changed, so the file is never a surprise."""
    if count:
        print(f"Updated {count} followed lot(s) in {path}.")
