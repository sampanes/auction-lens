"""The auction-lens command line entry point."""

from __future__ import annotations

import sys

from ..env_file import load_env_file
from . import commands
from .parser import (
    DAILY,
    DISCOVER,
    DOCTOR,
    FETCH,
    LOGISTICS,
    PROFILE,
    PROGRAM,
    PULL,
    RUN,
    SETUP,
    WATCH,
    WATCHLIST,
    build_parser,
)

COMMANDS = {
    SETUP: commands.setup,
    PROFILE: commands.profile,
    DOCTOR: commands.doctor,
    DAILY: commands.daily,
    RUN: commands.run,
    FETCH: commands.fetch,
    PULL: commands.pull,
    DISCOVER: commands.discover,
    LOGISTICS: commands.logistics,
    WATCH: commands.watch,
    WATCHLIST: commands.watchlist,
}

# Commands that validate or use provider and delivery settings load the ignored
# environment file first; the rest record or read local files.
COMMANDS_NEEDING_ENVIRONMENT = frozenset({DOCTOR, RUN, FETCH, DISCOVER, DAILY})

OPERATOR_ERROR = 2


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
