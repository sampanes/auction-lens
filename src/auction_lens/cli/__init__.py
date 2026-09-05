"""The auction-lens command line entry point."""

from __future__ import annotations

from ..env_file import load_env_file
from . import commands
from .parser import FETCH, LOGISTICS, RUN, build_parser

COMMANDS = {
    RUN: commands.run,
    FETCH: commands.fetch,
    LOGISTICS: commands.logistics,
}

# The logistics command records a local decision and never needs credentials.
COMMANDS_NEEDING_ENVIRONMENT = frozenset({RUN, FETCH})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in COMMANDS_NEEDING_ENVIRONMENT:
        load_env_file(args.env_file)
    return COMMANDS[args.command](args)


__all__ = ["build_parser", "main"]
