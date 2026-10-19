"""Asking the recorded closes about a product, with no sweep running.

The point of these is that the command adds no opinion of its own. What counts
as a close, which window qualifies, when a settled sale beats a reading and
which two titles are one product all belong to `pricing/closing_history`, so a
range printed by `comps` has to be the range a card would quote.
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from auction_lens.cli.comps import comps
from auction_lens.cli.exit_codes import OPERATOR_ERROR
from auction_lens.config.pricing import ValuationSourceConfig
from auction_lens.pricing.closing_history import (
    ClosingHistoryAdapter,
    recorded_products,
)
from support import temporary_database, temporary_directory

NOW = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)

_CONFIG_WITHOUT_HISTORY = """
[provider]
provider_id = "nellis"

[[interests]]
name = "example"
wants = "an example"
any_terms = ["example"]
"""


def _config_with_history(path):
    """A config whose only valuation source is the record this reads."""
    return _CONFIG_WITHOUT_HISTORY + f"""
[[valuation.sources]]
id = "closing-history"
adapter = "closing_history"
path = "{str(path).replace(chr(92), "/")}"
"""




def _source(path):
    return ValuationSourceConfig(
        source_id="closing-history",
        adapter="closing_history",
        enabled=True,
        settings={"path": str(path)},
    )


class RecordedProductTests(unittest.TestCase):
    """Grouping and ranking the record, which the command only prints."""

    def _write(self, database, rows, *, settled=()):
        """Store listings, a final reading each, and any settled sales."""
        with database.connect() as connection:
            for index, (title, price, minutes_early) in enumerate(rows):
                listing_id = str(index + 1)
                ends = NOW
                read_at = ends - timedelta(minutes=minutes_early)
                connection.execute(
                    "INSERT INTO listings (source, listing_id, title, url,"
                    " current_bid, estimated_retail, bid_count, ends_at, location,"
                    " conditions, image_url, first_seen, last_seen)"
                    " VALUES ('nellis', ?, ?, 'https://example.invalid/p/x', ?,"
                    " '100', 3, ?, 'Example', '', '', ?, ?)",
                    (
                        listing_id,
                        title,
                        str(price),
                        ends.isoformat(),
                        read_at.isoformat(),
                        read_at.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO price_history (source, listing_id, observed_at,"
                    " current_bid, bid_count) VALUES ('nellis', ?, ?, ?, 3)",
                    (listing_id, read_at.isoformat(), str(price)),
                )
            for listing_id, price in settled:
                connection.execute(
                    "INSERT INTO settled_closes (source, listing_id, final_bid,"
                    " bid_count, closed_at, scheduled_close, extended, settled_at)"
                    " VALUES ('nellis', ?, ?, 4, ?, ?, 0, ?)",
                    (
                        listing_id,
                        str(price),
                        NOW.isoformat(),
                        NOW.isoformat(),
                        NOW.isoformat(),
                    ),
                )

    def _records(self, rows, *, settled=(), phrase=""):
        with temporary_database() as database:
            self._write(database, rows, settled=settled)
            adapter = ClosingHistoryAdapter(_source(database.path))
            return recorded_products(adapter, phrase)

    def test_two_lots_of_one_product_become_one_record_with_a_range(self):
        records = self._records(
            [
                ("Example 8 Person Instant Tent", 20, 5),
                ("Example 8 Person Instant Tent", 60, 5),
            ]
        )

        (record,) = records
        self.assertEqual(len(record.prices), 2)
        self.assertEqual(record.low, Decimal("20.00"))
        self.assertEqual(record.high, Decimal("60.00"))
        self.assertEqual(record.typical, Decimal("40.00"))

    def test_a_reading_taken_too_early_is_not_a_close(self):
        # The same bar the adapter applies: earlier than the endpoint window, a
        # bid is a lower bound that happens to be the last one recorded.
        records = self._records([("Example 8 Person Instant Tent", 20, 600)])

        self.assertEqual(records, [])

    def test_the_warehouse_shouting_does_not_split_one_product_in_two(self):
        records = self._records(
            [
                ("Example 8 Person Instant Tent", 20, 5),
                ("*** STOCK PHOTO *** Example 8 Person Instant Tent", 30, 5),
            ]
        )

        self.assertEqual(len(records), 1, "one product, however it was titled")

    def test_the_plainest_title_stands_for_the_group(self):
        records = self._records(
            [
                ("Example 8 Person Instant Tent", 20, 5),
                ("*** STOCK PHOTO *** Example 8 Person Instant Tent", 30, 5),
            ]
        )

        self.assertEqual(records[0].title, "Example 8 Person Instant Tent")

    def test_a_settled_sale_replaces_the_readings_rather_than_joining_them(self):
        # Mixing a sale with a bid read before the hammer would pull the better
        # answer down by exactly the error settling exists to remove.
        records = self._records(
            [
                ("Example 8 Person Instant Tent", 20, 5),
                ("Example 8 Person Instant Tent", 30, 5),
            ],
            settled=[("1", 90)],
        )

        (record,) = records
        self.assertTrue(record.settled)
        self.assertEqual(record.prices, (Decimal("90.00"),))

    def test_the_best_evidenced_product_is_reported_first(self):
        records = self._records(
            [
                ("Example Thin Record", 50, 5),
                ("Example Deep Record", 10, 5),
                ("Example Deep Record", 20, 5),
            ]
        )

        self.assertEqual(records[0].title, "Example Deep Record")

    def test_a_phrase_narrows_the_record_to_one_product(self):
        records = self._records(
            [("Example Instant Tent", 20, 5), ("Example Cordless Drill", 30, 5)],
            phrase="instant",
        )

        (record,) = records
        self.assertEqual(record.title, "Example Instant Tent")

    def test_an_unbid_lot_says_nothing_about_what_a_product_fetches(self):
        records = self._records([("Example 8 Person Instant Tent", 0, 5)])

        self.assertEqual(records, [])


class CompsCommandTests(unittest.TestCase):
    """The command's own share: finding the source, and what it prints."""

    def _run(self, config_text, **arguments):
        with temporary_directory() as directory:
            config = directory / "local.toml"
            config.write_text(config_text, encoding="utf-8")
            defaults = {"config": str(config), "match": "", "minimum_closes": 1}
            settings = argparse.Namespace(**{**defaults, **arguments})
            output = io.StringIO()
            with redirect_stdout(output):
                code = comps(settings)
            return code, output.getvalue()

    def test_no_configured_history_source_is_an_operator_error_not_a_crash(self):
        code, output = self._run(_CONFIG_WITHOUT_HISTORY)

        self.assertEqual(code, OPERATOR_ERROR)
        self.assertIn("no closing-history valuation source", output)

    def test_an_empty_record_says_which_of_the_two_reasons_it_was(self):
        with temporary_database() as database:
            code, output = self._run(_config_with_history(database.path))

        self.assertEqual(code, 0)
        self.assertIn("No product has a recorded close yet", output)

    def test_a_phrase_that_matches_nothing_suggests_the_two_ways_out(self):
        with temporary_database() as database:
            code, output = self._run(
                _config_with_history(database.path), match="nothing-like-this"
            )

        self.assertEqual(code, 0)
        self.assertIn("nothing-like-this", output)
        self.assertIn("--minimum-closes 1", output)


