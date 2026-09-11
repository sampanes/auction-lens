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
    _add_config(profile)
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
    _add_config(doctor)
    _add_env_file(doctor)
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
    _add_config(daily)
    daily.add_argument(
        "--output", default=DEFAULT_INBOX, help="where the found lots are written"
    )
    _add_database(daily)
    _add_watchlist_file(daily)
    _add_env_file(daily)
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


def _add_run(subparsers) -> None:
    run = subparsers.add_parser(RUN, help="ingest listings, evaluate them, and render a report")
    run.add_argument("--input", required=True, help="canonical .json or .csv listing file")
    _add_config(run)
    _add_database(run)
    _add_watchlist_file(run)
    _add_env_file(run)
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
    _add_config(fetch)
    _add_env_file(fetch)


def _add_discover(subparsers) -> None:
    """One request per search term, and each one describes a page of lots."""
    discover = subparsers.add_parser(
        DISCOVER, help="ask the provider's search for lots and write them as canonical JSON"
    )
    _add_config(discover)
    discover.add_argument("--output", required=True, help="canonical .json file to write")
    discover.add_argument(
        "--search",
        action="append",
        default=[],
        metavar="TERM",
        help="search term; repeatable. Defaults to configured searches or interest terms",
    )
    _add_env_file(discover)


def _add_pull(subparsers) -> None:
    """Fetching saves pages; pulling reads them. Keeping the two apart means a
    parser can be corrected and re-run without asking the provider again."""
    pull = subparsers.add_parser(
        PULL, help="read saved provider pages into a canonical listing file"
    )
    _add_config(pull)
    pull.add_argument(
        "--input", required=True, help="a saved .html page, or a directory of them"
    )
    pull.add_argument("--output", required=True, help="canonical .json file to write")


def _add_logistics(subparsers) -> None:
    logistics = subparsers.add_parser(
        LOGISTICS, help="save or clear a handling decision for one listing"
    )
    _add_database(logistics)
    _add_lot_identity(logistics)
    logistics.add_argument(
        "--status",
        required=True,
        choices=LOGISTICS_STATUSES,
        help="whether you can actually collect this lot, or clear a saved answer",
    )
    logistics.add_argument(
        "--added-cost",
        default="0",
        help="what collecting it costs on top of the bid, such as a van hire",
    )
    logistics.add_argument("--note", default="", help="why, for when you have forgotten")


def _add_watch(subparsers) -> None:
    watch = subparsers.add_parser(
        WATCH, help="say what you think of one lot, or stop following it"
    )
    _add_watchlist_file(watch)
    _add_lot_identity(watch)
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
    _add_watchlist_file(watchlist)
    watchlist.add_argument(
        "--verdict", choices=VERDICTS, help="show only lots you decided one way"
    )
    watchlist.add_argument(
        "--email", action="store_true", help="email the selected lots after showing them"
    )
    _add_config(watchlist)
    _add_env_file(watchlist)
    _add_delivery_ledger(watchlist)


def _add_sold(subparsers) -> None:
    """The only question the observation database can answer that the report cannot."""
    sold = subparsers.add_parser(
        SOLD,
        help="show what closed lots were last seen at (a floor, not the hammer price)",
    )
    _add_database(sold)
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
    _add_config(sold)


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


# The flags below name the same four files on almost every command. Each is
# described once here, so "what is --config" has one answer rather than one per
# command, and a changed default is a single edit.


def _add_lot_identity(command) -> None:
    """Name one lot the way the report already offers it.

    Every report prints a Watch key, so pasting that back is the short path and
    the only one worth remembering. The two-flag spelling stays because scripts
    already hold the parts separately and have no key to paste.
    """
    command.add_argument(
        "--key",
        help="copyable SOURCE/LISTING-ID shown as Watch key in a report",
    )
    command.add_argument("--source", help="provider id; use with --listing-id")
    command.add_argument("--listing-id", help="provider listing id; use with --source")


def _add_config(command) -> None:
    """The file a person edits: the provider, their interests, and their limits."""
    command.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="TOML file holding the provider, your interests, and your limits",
    )


def _add_env_file(command) -> None:
    """The ignored file holding a contact address and any secrets."""
    command.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help="ignored KEY=VALUE file holding your contact address and any secrets",
    )


def _add_database(command) -> None:
    """What the tool remembers between runs, and nothing a person edits."""
    command.add_argument(
        "--database",
        default=DEFAULT_DATABASE,
        help="SQLite file remembering observations, prices, and handling decisions",
    )


def _add_watchlist_file(command) -> None:
    """The lots being followed. Named for the file, not the command of that name."""
    command.add_argument(
        "--watchlist",
        default=DEFAULT_WATCHLIST_FILE,
        help="ignored JSON file recording the lots you are following",
    )
