"""The command line, described in one place."""

from __future__ import annotations

import argparse

from ..models import OPERATOR_DECIDABLE, Verdict
from ..storage import DEFAULT_WATCHLIST_FILE

PROGRAM = "auction-lens"
DEFAULT_DATABASE = "data/auction-lens.sqlite3"
DEFAULT_ENV_FILE = ".env"

RUN = "run"
FETCH = "fetch"
PULL = "pull"
LOGISTICS = "logistics"
WATCH = "watch"
WATCHLIST = "watchlist"

# Everything an operator may record, plus the word that removes a past answer.
CLEAR = "clear"
LOGISTICS_STATUSES = (*(status.value for status in OPERATOR_DECIDABLE), CLEAR)

# The same shape for the watchlist: every verdict, plus the word that forgets
# a lot. A verdict is the person's own word; the provider's condition tags
# are the lot's, and nothing on the command line sets those.
DROP = "drop"
VERDICTS = tuple(verdict.value for verdict in Verdict)
WATCH_ACTIONS = (*VERDICTS, DROP)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Normalize, score, remember, and report auction listings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_run(subparsers)
    _add_fetch(subparsers)
    _add_pull(subparsers)
    _add_logistics(subparsers)
    _add_watch(subparsers)
    _add_watchlist(subparsers)
    return parser


def _add_run(subparsers) -> None:
    run = subparsers.add_parser(RUN, help="ingest listings, evaluate them, and render a report")
    run.add_argument("--input", required=True, help="canonical .json or .csv listing file")
    run.add_argument("--config", required=True, help="TOML provider and scoring configuration")
    run.add_argument("--database", default=DEFAULT_DATABASE)
    run.add_argument(
        "--watchlist",
        default=DEFAULT_WATCHLIST_FILE,
        help="ignored JSON file that collects a price reading per reported lot",
    )
    run.add_argument(
        "--env-file", default=DEFAULT_ENV_FILE, help="optional local KEY=VALUE settings file"
    )
    run.add_argument(
        "--email", action="store_true", help="send the report using configured SMTP settings"
    )


def _add_fetch(subparsers) -> None:
    fetch = subparsers.add_parser(FETCH, help="fetch one authorized public provider page")
    fetch.add_argument("--config", required=True, help="TOML provider configuration")
    fetch.add_argument(
        "--env-file", default=DEFAULT_ENV_FILE, help="optional local KEY=VALUE settings file"
    )


def _add_pull(subparsers) -> None:
    """Fetching saves pages; pulling reads them. Keeping the two apart means a
    parser can be corrected and re-run without asking the provider again."""
    pull = subparsers.add_parser(
        PULL, help="read saved provider pages into a canonical listing file"
    )
    pull.add_argument("--config", required=True, help="TOML provider configuration")
    pull.add_argument(
        "--input", required=True, help="a saved .html page, or a directory of them"
    )
    pull.add_argument("--output", required=True, help="canonical .json file to write")


def _add_logistics(subparsers) -> None:
    logistics = subparsers.add_parser(
        LOGISTICS, help="save or clear a handling decision for one listing"
    )
    logistics.add_argument("--database", default=DEFAULT_DATABASE)
    logistics.add_argument("--source", required=True)
    logistics.add_argument("--listing-id", required=True)
    logistics.add_argument("--status", required=True, choices=LOGISTICS_STATUSES)
    logistics.add_argument("--added-cost", default="0")
    logistics.add_argument("--note", default="")


def _add_watch(subparsers) -> None:
    watch = subparsers.add_parser(
        WATCH, help="say what you think of one lot, or stop following it"
    )
    watch.add_argument("--watchlist", default=DEFAULT_WATCHLIST_FILE)
    watch.add_argument("--source", required=True)
    watch.add_argument("--listing-id", required=True)
    watch.add_argument("--verdict", choices=WATCH_ACTIONS)
    watch.add_argument("--estimate", help="what the lot is worth to you, all in")
    watch.add_argument("--note", help="anything the other fields cannot say")


def _add_watchlist(subparsers) -> None:
    """Reading the file is the common case, so it is its own command."""
    watchlist = subparsers.add_parser(WATCHLIST, help="show the lots you are following")
    watchlist.add_argument("--watchlist", default=DEFAULT_WATCHLIST_FILE)
    watchlist.add_argument(
        "--verdict", choices=VERDICTS, help="show only lots you decided one way"
    )
