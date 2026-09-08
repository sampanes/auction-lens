"""One analysis run, from listings to candidates."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from auction_lens.pipeline import analyze_listings
from auction_lens.storage import LogisticsDecisionStore, ObservationStore, WatchlistStore
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


class ReportCapTests(unittest.TestCase):
    """Cutting the tail off a ranking without lying about having done it."""

    def setUp(self):
        self.listings = example_listings()

    def test_without_a_cap_every_match_is_reported(self):
        result = self._run(example_config())
        self.assertEqual(len(result.candidates), result.matches_found)
        self.assertEqual(result.matches_not_shown, 0)

    def test_a_cap_keeps_the_best_and_counts_what_it_dropped(self):
        uncapped = self._run(example_config())
        self.assertGreater(uncapped.matches_found, 1)

        capped = self._run(self._config_with_cap(1))
        self.assertEqual(len(capped.candidates), 1)
        self.assertEqual(capped.matches_found, uncapped.matches_found)
        self.assertEqual(capped.matches_not_shown, uncapped.matches_found - 1)

    def test_the_one_kept_is_the_highest_priority_not_merely_the_first(self):
        best = max(self._run(example_config()).candidates, key=lambda item: item.priority)
        kept = self._run(self._config_with_cap(1)).candidates[0]
        self.assertEqual(kept.priority, best.priority)

    def test_a_cap_larger_than_the_findings_hides_nothing(self):
        result = self._run(self._config_with_cap(500))
        self.assertEqual(result.matches_not_shown, 0)

    def test_the_watchlist_follows_only_what_was_actually_shown(self):
        # Following a lot the report never mentioned would start a price
        # history nobody asked for and nobody would recognise later.
        with temporary_database() as database:
            result = analyze_listings(
                self.listings,
                self._config_with_cap(1),
                observations=ObservationStore(database),
                decisions=LogisticsDecisionStore(database),
                watchlist=WatchlistStore(Path(database.path).parent / "watchlist.json"),
            )
        self.assertEqual(result.lots_followed, 1)

    def _config_with_cap(self, limit):
        config = example_config()
        return replace(config, reports=replace(config.reports, max_items=limit))

    def _run(self, config):
        with temporary_database() as database:
            return analyze_listings(
                self.listings,
                config,
                observations=ObservationStore(database),
                decisions=LogisticsDecisionStore(database),
            )


if __name__ == "__main__":
    unittest.main()
