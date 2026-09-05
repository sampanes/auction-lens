"""What the SQLite database remembers between runs."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from auction_lens.models import LogisticsDecision
from auction_lens.storage import LogisticsDecisionStore, ObservationStore
from support import SOUNDBAR, example_listings, temporary_database


class ObservationStoreTests(unittest.TestCase):
    def setUp(self):
        self.listing = example_listings()[SOUNDBAR]

    def test_first_sighting_is_new_and_the_second_is_not(self):
        with temporary_database() as database:
            store = ObservationStore(database)
            first = store.observe(self.listing)
            second = store.observe(self.listing)
        self.assertTrue(first.is_new)
        self.assertFalse(second.is_new)
        self.assertFalse(second.price_changed)

    def test_a_moved_bid_is_reported_with_the_previous_price(self):
        with temporary_database() as database:
            store = ObservationStore(database)
            store.observe(self.listing)
            changed = store.observe(
                replace(
                    self.listing,
                    current_bid=Decimal("19.00"),
                    observed_at=self.listing.observed_at + timedelta(seconds=1),
                )
            )
        self.assertTrue(changed.price_changed)
        self.assertEqual(changed.previous_bid, Decimal("18.00"))

    def test_price_history_keeps_one_row_per_observation_time(self):
        with temporary_database() as database:
            store = ObservationStore(database)
            store.observe(self.listing)
            store.observe(self.listing)
            store.observe(
                replace(self.listing, observed_at=self.listing.observed_at + timedelta(hours=1))
            )
            with database.connect() as connection:
                rows = connection.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        self.assertEqual(rows, 2)


class LogisticsDecisionStoreTests(unittest.TestCase):
    def test_a_decision_round_trips_and_can_be_cleared(self):
        decision = LogisticsDecision(
            status="feasible", added_cost=Decimal("12.50"), note="generic test plan"
        )
        with temporary_database() as database:
            store = LogisticsDecisionStore(database)
            store.save("example", "large-1", decision)
            loaded = store.get("example", "large-1")
            store.clear("example", "large-1")
            cleared = store.get("example", "large-1")
        self.assertEqual(loaded, decision)
        self.assertIsNone(cleared)

    def test_an_undecidable_status_is_refused(self):
        with temporary_database() as database:
            store = LogisticsDecisionStore(database)
            with self.assertRaisesRegex(ValueError, "feasible or infeasible"):
                store.save("example", "large-1", LogisticsDecision(status="ordinary"))


if __name__ == "__main__":
    unittest.main()
