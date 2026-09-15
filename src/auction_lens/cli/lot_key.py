"""Parse the lot key shared by the logistics and watch commands."""

from __future__ import annotations

import argparse


def lot_identity(args: argparse.Namespace) -> tuple[str, str]:
    """Accept either a report's copyable key or its two separate parts."""
    if args.key:
        if args.source or args.listing_id:
            raise ValueError("--key cannot be combined with --source or --listing-id")
        source, separator, listing_id = args.key.strip().partition("/")
        if not separator or not source or not listing_id:
            raise ValueError("--key must be the SOURCE/LISTING-ID shown in the report")
        return source, listing_id
    if not args.source or not args.listing_id:
        raise ValueError("use --key SOURCE/LISTING-ID, or both --source and --listing-id")
    return args.source, args.listing_id
