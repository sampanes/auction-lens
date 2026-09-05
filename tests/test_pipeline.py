"""One analysis run, from listings to candidates."""

from __future__ import annotations

import unittest
from dataclasses import replace

from auction_lens.pipeline import analyze_listings
from auction_lens.storage import LogisticsDecisionStore, ObservationStore
from auction_lens.valuation import ValuationEngine
from support import SOUNDBAR, example_config, example_listings, temporary_database


class AnalysisRunTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_every_listing_of_this_provider_is_observed_and_scored(self):
        with temporary_database() as database:
            result = self._run(database, self.listings)
        self.assertEqual(result.listings_read, 2)
        self.assertEqual(result.listings_scored, 2)
        self.assertTrue(result.candidates)

    def test_listings_from_another_provider_are_counted_and_left_alone(self):
        other = replace(self.listings[SOUNDBAR], source="other-provider")
        with temporary_database() as database:
            result = self._run(database, [*self.listings, other])
        self.assertEqual(result.listings_read, 3)
        self.assertEqual(result.listings_scored, 2)
        self.assertEqual(result.listings_from_other_providers, 1)

    def test_a_second_run_no_longer_reports_the_listings_as_new(self):
        with temporary_database() as database:
            self._run(database, self.listings)
            second = self._run(database, self.listings)
        self.assertFalse(any(item.change.is_new for item in second.candidates))

    def test_valuation_is_attached_to_every_candidate(self):
        engine = ValuationEngine(self.config.valuation)
        with temporary_database() as database:
            result = self._run(database, self.listings, valuation_engine=engine)
        soundbar = next(
            item for item in result.candidates if item.listing.listing_id == "synthetic-001"
        )
        self.assertTrue(soundbar.valuation.bands)

    def _run(self, database, listings, valuation_engine=None):
        return analyze_listings(
            listings,
            self.config,
            observations=ObservationStore(database),
            decisions=LogisticsDecisionStore(database),
            valuation_engine=valuation_engine,
        )


if __name__ == "__main__":
    unittest.main()
