"""The auction-lens command line entry point."""

from __future__ import annotations

import sys

from .. import collect, daily, doctor, setup
from ..config.environment import load_env_file
from ..config.profile_wizard import profile
from . import feedback, logistics, sold, watchlist
from .exit_codes import OPERATOR_ERROR
from .parser import (
    DAILY,
    DISCOVER,
    DOCTOR,
    FEEDBACK,
    FETCH,
    LOGISTICS,
    PROFILE,
    PROGRAM,
    PULL,
    RUN,
    SETUP,
    SOLD,
    WATCH,
    WATCHLIST,
    build_parser,
)

# The one door. Every command is one function, named for the word an operator
# types, and this is the only place that knows which is which.
COMMANDS = {
    SETUP: setup.setup,
    PROFILE: profile,
    DOCTOR: doctor.doctor,
    DAILY: daily.daily,
    RUN: daily.run,
    FETCH: collect.fetch,
    PULL: collect.pull,
    DISCOVER: collect.discover,
    LOGISTICS: logistics.logistics,
    WATCH: watchlist.watch,
    WATCHLIST: watchlist.watchlist,
    FEEDBACK: feedback.feedback,
    SOLD: sold.sold,
}

# Commands that validate or use provider and delivery settings load the ignored
# environment file first; the rest record or read local files.
COMMANDS_NEEDING_ENVIRONMENT = frozenset({DOCTOR, RUN, FETCH, DISCOVER, DAILY})


def main(argv: list[str] | None = None) -> int:
    """Dispatch a command, leaving errors intact for callers and tests."""
    args = build_parser().parse_args(argv)
    if args.command in COMMANDS_NEEDING_ENVIRONMENT or getattr(args, "email", False):
        load_env_file(args.env_file)
    return COMMANDS[args.command](args)


def console(argv: list[str] | None = None) -> int:
    """Run the human-facing CLI with concise, actionable operator errors."""
    try:
        return main(argv)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"{PROGRAM}: error: {error}", file=sys.stderr)
        return OPERATOR_ERROR


__all__ = ["build_parser", "console", "main"]
