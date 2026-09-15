"""Answer the command-line question about recently observed closing prices."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from ..config.load import load_config
from ..history.database import Database
from ..history.sales import ClosingPriceStore, render_closing_prices
from ..matching.text import mentions
from .exit_codes import SUCCESS


def sold(args: argparse.Namespace) -> int:
    """Show what closed lots were last seen going for, tightest reading first."""
    config = load_config(args.config)
    database = Database.at(args.database)
    database.initialize()
    prices = ClosingPriceStore(database).closed_by(datetime.now(UTC))
    if args.match:
        prices = tuple(
            price for price in prices if mentions(price.title.lower(), args.match.lower())
        )
    print(
        render_closing_prices(
            prices,
            config.acquisition.zone,
            within_minutes=args.within_minutes,
            limit=args.limit,
        ),
        end="",
    )
    return SUCCESS
