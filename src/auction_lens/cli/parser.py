"""The command line, described in one place."""

from __future__ import annotations

import argparse

PROGRAM = "auction-lens"
DEFAULT_DATABASE = "data/auction-lens.sqlite3"
DEFAULT_ENV_FILE = ".env"

RUN = "run"
FETCH = "fetch"
LOGISTICS = "logistics"

CLEAR = "clear"
LOGISTICS_STATUSES = ("feasible", "infeasible", CLEAR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Normalize, score, remember, and report auction listings.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_run(subparsers)
    _add_fetch(subparsers)
    _add_logistics(subparsers)
    return parser


def _add_run(subparsers) -> None:
    run = subparsers.add_parser(RUN, help="ingest listings, evaluate them, and render a report")
    run.add_argument("--input", required=True, help="canonical .json or .csv listing file")
    run.add_argument("--config", required=True, help="TOML provider and scoring configuration")
    run.add_argument("--database", default=DEFAULT_DATABASE)
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
