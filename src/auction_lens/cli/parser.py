"""The command line, described in one place."""

from __future__ import annotations

import argparse

from .. import __version__
from ..models import OPERATOR_DECIDABLE, Verdict
from ..reporting import DEFAULT_WITHIN_MINUTES
from ..storage import DEFAULT_DELIVERY_LEDGER, DEFAULT_WATCHLIST_FILE

PROGRAM = "auction-lens"
DEFAULT_DATABASE = "data/auction-lens.sqlite3"
DEFAULT_ENV_FILE = ".env"
# The one configuration a person actually edits. Every command defaults to
# it, so the flag only has to be typed when working on something else.
DEFAULT_CONFIG = "config/local.toml"
DEFAULT_INBOX = "data/inbox/listings.json"
EXAMPLE_CONFIG = "config/providers/nellis.example.toml"

SETUP = "setup"
PROFILE = "profile"
DOCTOR = "doctor"
DAILY = "daily"
RUN = "run"
FETCH = "fetch"
PULL = "pull"
DISCOVER = "discover"
LOGISTICS = "logistics"
WATCH = "watch"
WATCHLIST = "watchlist"
SOLD = "sold"

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
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    # The operator-facing doors come first, before the tools for one job each.
    _add_setup(subparsers)
    _add_profile(subparsers)
    _add_doctor(subparsers)
    _add_daily(subparsers)
    _add_run(subparsers)
    _add_fetch(subparsers)
    _add_discover(subparsers)
    _add_pull(subparsers)
    _add_logistics(subparsers)
    _add_watch(subparsers)
    _add_watchlist(subparsers)
    _add_sold(subparsers)
    return parser


def _add_setup(subparsers) -> None:
    """First command on a new machine: make the files git cannot carry."""
    setup = subparsers.add_parser(
        SETUP, help="create the ignored config and .env this machine needs"
    )
    setup.add_argument("--config", default=DEFAULT_CONFIG, help="configuration to create")
    setup.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="settings file to create")
    setup.add_argument(
        "--email",
        action="store_true",
        help="also ask for the mail settings, without echoing the password",
    )


def _add_profile(subparsers) -> None:
    """Read back what the stable, human-owned configuration means."""
    profile = subparsers.add_parser(
        PROFILE, help="read interests and safely edit practical limits"
    )
    profile.add_argument("--config", default=DEFAULT_CONFIG)
    action = profile.add_mutually_exclusive_group()
    action.add_argument(
        "--edit",
        action="store_true",
        help="interactively update the practical large-item limits",
    )
    action.add_argument(
        "--restore",
        action="store_true",
        help="preview and restore the configuration saved before the last edit",
    )


def _add_doctor(subparsers) -> None:
    """A dry local preflight for an unattended daily run."""
    doctor = subparsers.add_parser(
        DOCTOR, help="check configuration and credentials without contacting anything"
    )
    doctor.add_argument("--config", default=DEFAULT_CONFIG)
    doctor.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    _add_delivery_ledger(doctor, allow_repeat=False)
    doctor.add_argument(
        "--email",
        action="store_true",
        help="check email readiness and fail if it is disabled",
    )
    doctor.add_argument(
        "--webhook",
        action="store_true",
        help="check webhook readiness and fail if it is disabled",
    )


def _add_daily(subparsers) -> None:
    """The whole day's work in one word.

    Discovery and analysis remain separate commands because each is useful on
    its own, but nobody wants to type both every morning.
    """
    daily = subparsers.add_parser(
        DAILY, help="find lots, score them, and report what matters"
    )
    daily.add_argument("--config", default=DEFAULT_CONFIG)
    daily.add_argument(
        "--output", default=DEFAULT_INBOX, help="where the found lots are written"
    )
    daily.add_argument("--database", default=DEFAULT_DATABASE)
    daily.add_argument("--watchlist", default=DEFAULT_WATCHLIST_FILE)
    daily.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    _add_delivery_ledger(daily)
    daily.add_argument(
        "--search", action="append", default=[], metavar="TERM",
        help=(
            "search term; repeatable. Defaults to configured searches or active "
            "interest terms"
        ),
    )
    _add_visiting(daily)
    daily.add_argument("--email", action="store_true", help="send the report as well")
    daily.add_argument(
        "--webhook", action="store_true", help="post the report to chat as well"
    )


def _add_visiting(command) -> None:
    """Both reporting commands take it, because it is one fact about the day."""
    command.add_argument(
        "--visiting",
        action="append",
        default=[],
        metavar="BRANCH",
        help="a branch you are already going to today; repeatable. Its lots are "
        "held to the ordinary bar rather than the higher far-branch one",
    )


def _add_run(subparsers) -> None:
    run = subparsers.add_parser(RUN, help="ingest listings, evaluate them, and render a report")
    run.add_argument("--input", required=True, help="canonical .json or .csv listing file")
    run.add_argument(
        "--config", default=DEFAULT_CONFIG, help="TOML provider and scoring configuration"
    )
    run.add_argument("--database", default=DEFAULT_DATABASE)
    run.add_argument(
        "--watchlist",
        default=DEFAULT_WATCHLIST_FILE,
        help="ignored JSON file that collects a price reading per reported lot",
    )
    run.add_argument(
        "--env-file", default=DEFAULT_ENV_FILE, help="optional local KEY=VALUE settings file"
    )
    _add_delivery_ledger(run)
    _add_visiting(run)
    run.add_argument(
        "--email", action="store_true", help="send the report using configured SMTP settings"
    )
    run.add_argument(
        "--webhook", action="store_true", help="post the report to the configured chat webhook"
    )


def _add_fetch(subparsers) -> None:
    fetch = subparsers.add_parser(FETCH, help="fetch one authorized public provider page")
    fetch.add_argument("--config", default=DEFAULT_CONFIG, help="TOML provider configuration")
    fetch.add_argument(
        "--env-file", default=DEFAULT_ENV_FILE, help="optional local KEY=VALUE settings file"
    )


def _add_discover(subparsers) -> None:
    """One request per search term, and each one describes a page of lots."""
    discover = subparsers.add_parser(
        DISCOVER, help="ask the provider's search for lots and write them as canonical JSON"
    )
    discover.add_argument(
        "--config", default=DEFAULT_CONFIG, help="TOML provider configuration"
    )
    discover.add_argument("--output", required=True, help="canonical .json file to write")
    discover.add_argument(
        "--search",
        action="append",
        default=[],
        metavar="TERM",
        help="search term; repeatable. Defaults to configured searches or interest terms",
    )
    discover.add_argument(
        "--env-file", default=DEFAULT_ENV_FILE, help="optional local KEY=VALUE settings file"
    )


def _add_pull(subparsers) -> None:
    """Fetching saves pages; pulling reads them. Keeping the two apart means a
    parser can be corrected and re-run without asking the provider again."""
    pull = subparsers.add_parser(
        PULL, help="read saved provider pages into a canonical listing file"
    )
    pull.add_argument("--config", default=DEFAULT_CONFIG, help="TOML provider configuration")
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
    watch.add_argument(
        "--key",
        help="copyable SOURCE/LISTING-ID shown as Watch key in a report",
    )
    watch.add_argument("--source", help="provider id; use with --listing-id")
    watch.add_argument("--listing-id", help="provider listing id; use with --source")
    watch.add_argument("--verdict", choices=WATCH_ACTIONS)
    watch.add_argument("--estimate", help="what the lot is worth to you, all in")
    watch.add_argument("--note", help="anything the other fields cannot say")
    fulfillments = watch.add_mutually_exclusive_group()
    fulfillments.add_argument(
        "--fulfills",
        action="append",
        metavar="INTEREST",
        help="when won, assign this lot to a recorded interest; repeatable",
    )
    fulfillments.add_argument(
        "--clear-fulfillments",
        action="store_true",
        help=(
            "record that this lot fulfilled none; reopens any saved "
            "interest allocation"
        ),
    )


def _add_watchlist(subparsers) -> None:
    """Reading the file is the common case, so it is its own command."""
    watchlist = subparsers.add_parser(WATCHLIST, help="show the lots you are following")
    watchlist.add_argument("--watchlist", default=DEFAULT_WATCHLIST_FILE)
    watchlist.add_argument(
        "--verdict", choices=VERDICTS, help="show only lots you decided one way"
    )
    watchlist.add_argument(
        "--email", action="store_true", help="email the selected lots after showing them"
    )
    watchlist.add_argument(
        "--config", default=DEFAULT_CONFIG, help="TOML configuration, used when emailing"
    )
    watchlist.add_argument(
        "--env-file", default=DEFAULT_ENV_FILE, help="optional local KEY=VALUE settings file"
    )
    _add_delivery_ledger(watchlist)


def _add_sold(subparsers) -> None:
    """The only question the observation database can answer that the report cannot."""
    sold = subparsers.add_parser(
        SOLD,
        help="show what closed lots were last seen at (a floor, not the hammer price)",
    )
    sold.add_argument("--database", default=DEFAULT_DATABASE)
    sold.add_argument(
        "--within-minutes",
        type=int,
        default=DEFAULT_WITHIN_MINUTES,
        help="how close to the close a reading must be to be worth quoting",
    )
    sold.add_argument(
        "--match",
        help="show only lots whose title says this term",
    )
    sold.add_argument(
        "--limit", type=int, help="show only the tightest readings, not every one"
    )
    sold.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="TOML configuration, read for the timezone closing times are shown in",
    )


def _add_delivery_ledger(command, *, allow_repeat: bool = True) -> None:
    """Give every outbound report one private receipt ledger and one override."""
    command.add_argument(
        "--delivery-ledger",
        default=DEFAULT_DELIVERY_LEDGER,
        help="private receipt database used to suppress unchanged deliveries",
    )
    if allow_repeat:
        command.add_argument(
            "--repeat-delivery",
            action="store_true",
            help="send the current report even when this destination already received it",
        )
