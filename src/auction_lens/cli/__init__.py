"""The auction-lens command line entry point."""

from __future__ import annotations

import sys

from ..env_file import load_env_file
from . import commands
from .parser import FETCH, LOGISTICS, PROGRAM, RUN, build_parser

COMMANDS = {
    RUN: commands.run,
    FETCH: commands.fetch,
    LOGISTICS: commands.logistics,
}

# The logistics command records a local decision and never needs credentials.
COMMANDS_NEEDING_ENVIRONMENT = frozenset({RUN, FETCH})

OPERATOR_ERROR = 2


def main(argv: list[str] | None = None) -> int:
    """Dispatch a command, leaving errors intact for callers and tests."""
    args = build_parser().parse_args(argv)
    if args.command in COMMANDS_NEEDING_ENVIRONMENT:
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
