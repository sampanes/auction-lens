"""What closed lots were last seen going for, and how much that is worth.

Every assertion here is really about one thing: a price is only as good as the
moment it was read. The record refuses to exist without that moment, the query
picks the reading nearest the close, and the rendering keeps saying how near it
was. Take any one of those away and the number quietly becomes a guess.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from auction_lens.ingest import dated
from auction_lens.models import ClosingPrice, Listing
from auction_lens.reporting import render_closing_prices
from auction_lens.storage import ClosingPriceStore, ObservationStore
from support import REPORT_ZONE, SOUNDBAR, example_listings, temporary_database

CLOSES_AT = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
AFTER_EVERY_CLOSE = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)


def closing_price(**overrides) -> ClosingPrice:
    """One record with sensible parts, so a test only states what it is about."""
    settings = {
        "source": "nellis",
        "listing_id": "900000101",
        "title": "Makita Sliding Compound Miter Saw",
        "url": "https://example.invalid/p/miter-saw",
        "last_bid": Decimal("159.00"),
        "ends_at": CLOSES_AT,
        "last_seen_at": CLOSES_AT - timedelta(minutes=3),
        "bid_count": 18,
        "estimated_retail": Decimal("739.00"),
    }
    settings.update(overrides)
    return ClosingPrice(**settings)


class ClosingPriceRecordTests(unittest.TestCase):
    def test_how_late_the_reading_was_is_reported_in_whole_minutes(self):
        price = closing_price(last_seen_at=CLOSES_AT - timedelta(minutes=3, seconds=40))
        self.assertEqual(price.seen_minutes_before_close, 3)

    def test_the_floor_is_compared_with_the_provider_estimate(self):
        price = closing_price(last_bid=Decimal("100"), estimated_retail=Decimal("400"))
        self.assertEqual(price.share_of_retail, Decimal("0.25"))

    def test_a_lot_with_no_estimate_is_not_given_an_invented_one(self):
        self.assertIsNone(closing_price(estimated_retail=None).share_of_retail)

    def test_a_reading_taken_after_the_close_is_not_a_closing_price(self):
        # It describes a different auction, a relisting, or a stale page. The
        # record refuses it rather than letting a caller decide case by case.
        with self.assertRaises(ValueError):
            closing_price(last_seen_at=CLOSES_AT + timedelta(minutes=1))

    def test_the_key_is_the_one_a_person_types_back_at_a_command(self):
        self.assertEqual(closing_price().key, "nellis/900000101")


class DatedRowTests(unittest.TestCase):
    """The stamp every closing price rests on."""

    def setUp(self):
        self.row = {
            "source": "nellis",
            "listing_id": "900000101",
            "title": "Soundbar",
            "url": "https://example.invalid/p/soundbar",
            "current_bid": "18.00",
        }

    def test_a_row_says_when_it_was_true(self):
        (stamped,) = dated([self.row], CLOSES_AT)
        self.assertEqual(Listing.from_mapping(stamped).observed_at, CLOSES_AT)

    def test_an_undated_row_falls_back_to_now_and_so_loses_the_fact(self):
        # The fallback is why the stamp has to be applied at the boundary: a row
        # that arrives without one cannot be distinguished from a fresh one.
        before = datetime.now(UTC)
        self.assertGreaterEqual(Listing.from_mapping(self.row).observed_at, before)

    def test_the_original_row_is_left_alone(self):
        dated([self.row], CLOSES_AT)
        self.assertNotIn("observed_at", self.row)


class ClosingPriceStoreTests(unittest.TestCase):
    def setUp(self):
        self.listing = replace(
            example_listings()[SOUNDBAR],
            ends_at=CLOSES_AT,
            current_bid=Decimal("18.00"),
        )

    def _looked_at(self, database, *readings):
        """Record one lot as it was seen at each of these times and prices."""
        store = ObservationStore(database)
        for seen_at, bid in readings:
            store.observe(
                replace(self.listing, observed_at=seen_at, current_bid=Decimal(bid))
            )

    def test_the_reading_nearest_the_close_is_the_one_reported(self):
        with temporary_database() as database:
            self._looked_at(
                database,
                (CLOSES_AT - timedelta(hours=6), "18.00"),
                (CLOSES_AT - timedelta(minutes=2), "159.00"),
            )
            (price,) = ClosingPriceStore(database).closed_by(AFTER_EVERY_CLOSE)

        self.assertEqual(price.last_bid, Decimal("159.00"))
        self.assertEqual(price.seen_minutes_before_close, 2)

    def test_a_reading_taken_after_the_close_is_ignored(self):
        with temporary_database() as database:
            self._looked_at(
                database,
                (CLOSES_AT - timedelta(minutes=10), "159.00"),
                (CLOSES_AT + timedelta(hours=1), "900.00"),
            )
            (price,) = ClosingPriceStore(database).closed_by(AFTER_EVERY_CLOSE)

        self.assertEqual(price.last_bid, Decimal("159.00"))

    def test_a_lot_still_open_has_no_closing_price(self):
        with temporary_database() as database:
            self._looked_at(database, (CLOSES_AT - timedelta(hours=1), "18.00"))
            prices = ClosingPriceStore(database).closed_by(
                CLOSES_AT - timedelta(minutes=1)
            )
        self.assertEqual(prices, ())

    def test_a_lot_that_never_said_when_it_closes_is_left_out(self):
        self.listing = replace(self.listing, ends_at=None)
        with temporary_database() as database:
            self._looked_at(database, (CLOSES_AT - timedelta(hours=1), "18.00"))
            prices = ClosingPriceStore(database).closed_by(AFTER_EVERY_CLOSE)
        self.assertEqual(prices, ())

    def test_a_lot_seen_only_after_it_closed_is_left_out(self):
        with temporary_database() as database:
            self._looked_at(database, (CLOSES_AT + timedelta(minutes=5), "159.00"))
            prices = ClosingPriceStore(database).closed_by(AFTER_EVERY_CLOSE)
        self.assertEqual(prices, ())


class ClosingPriceReportTests(unittest.TestCase):
    def _render(self, prices, **overrides):
        return render_closing_prices(tuple(prices), REPORT_ZONE, **overrides)

    def test_a_tight_reading_is_quoted_as_a_floor(self):
        report = self._render([closing_price()])
        self.assertIn("at least $159", report)
        self.assertIn("sold for at least", report)
        self.assertIn("seen 3m before it closed", report)

    def test_the_estimate_is_shown_beside_the_floor(self):
        report = self._render([closing_price()])
        self.assertIn("$739 estimated retail (22%)", report)

    def test_the_lot_can_be_identified_and_looked_up(self):
        report = self._render([closing_price()])
        self.assertIn("nellis/900000101", report)
        self.assertIn("Makita Sliding Compound Miter Saw", report)

    def test_tighter_readings_are_read_first(self):
        loose = closing_price(
            listing_id="900000102", last_seen_at=CLOSES_AT - timedelta(minutes=20)
        )
        report = self._render([loose, closing_price()])
        self.assertLess(report.index("900000101"), report.index("900000102"))

    def test_a_stale_reading_is_counted_rather_than_dropped_in_silence(self):
        stale = closing_price(
            listing_id="900000102", last_seen_at=CLOSES_AT - timedelta(hours=6)
        )
        report = self._render([closing_price(), stale])
        self.assertNotIn("900000102", report)
        self.assertIn("1 closed lot(s) left out", report)

    def test_a_trimmed_list_says_it_was_trimmed(self):
        second = closing_price(
            listing_id="900000102", last_seen_at=CLOSES_AT - timedelta(minutes=5)
        )
        report = self._render([closing_price(), second], limit=1)
        self.assertIn("1 further tight reading(s) not shown", report)

    def test_an_empty_database_says_so_plainly(self):
        self.assertIn("No lot in the database has closed yet", self._render([]))

    def test_only_stale_readings_names_the_fix(self):
        stale = closing_price(last_seen_at=CLOSES_AT - timedelta(hours=6))
        report = self._render([stale])
        self.assertIn("no price worth quoting", report)
        self.assertIn("nearer the hour lots close", report)


if __name__ == "__main__":
    unittest.main()
