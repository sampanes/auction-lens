"""One analysis run, from listings to candidates."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auction_lens.models import CandidateCategory, InterestRef, Verdict, WatchedItem
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


class ClosingWindowTests(unittest.TestCase):
    """A report is a list of things that can still be bid on."""

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()
        self.now = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)

    def test_a_lot_that_has_already_closed_never_reaches_scoring(self):
        closed = [
            replace(listing, ends_at=self.now - timedelta(minutes=1))
            for listing in self.listings
        ]
        result = self._run(closed)
        self.assertEqual(result.listings_scored, 0)
        self.assertEqual(result.lots_outside_the_window, len(closed))
        self.assertFalse(result.candidates)

    def test_a_lot_still_open_is_scored_when_no_window_is_configured(self):
        far = [
            replace(listing, ends_at=self.now + timedelta(days=30))
            for listing in self.listings
        ]
        result = self._run(far)
        self.assertEqual(result.listings_scored, len(far))
        self.assertEqual(result.lots_outside_the_window, 0)

    def test_a_window_keeps_what_closes_inside_it_and_sets_the_rest_aside(self):
        soon, later = self.listings[0], self.listings[1]
        soon = replace(soon, ends_at=self.now + timedelta(hours=2))
        later = replace(later, ends_at=self.now + timedelta(hours=20))

        result = self._run([soon, later], within_hours=6)

        self.assertEqual(result.listings_scored, 1)
        self.assertEqual(result.lots_outside_the_window, 1)
        self.assertTrue(
            all(item.listing.listing_id == soon.listing_id for item in result.candidates)
        )

    def test_a_lot_stating_no_closing_time_is_kept_rather_than_guessed_about(self):
        # Silence is not a reason to hide something the operator asked for.
        silent = [replace(listing, ends_at=None) for listing in self.listings]
        result = self._run(silent, within_hours=1)
        self.assertEqual(result.listings_scored, len(silent))
        self.assertEqual(result.lots_outside_the_window, 0)

    def test_the_window_is_not_confused_with_another_provider_s_listings(self):
        # Both counts are subtracted from the same total, so an error in one
        # would silently show up as the other.
        stranger = replace(self.listings[SOUNDBAR], source="other-provider")
        closed = replace(self.listings[1], ends_at=self.now - timedelta(minutes=1))

        result = self._run([self.listings[SOUNDBAR], stranger, closed])

        self.assertEqual(result.listings_read, 3)
        self.assertEqual(result.listings_scored, 1)
        self.assertEqual(result.lots_outside_the_window, 1)
        self.assertEqual(result.listings_from_other_providers, 1)

    def test_a_future_lot_is_observed_before_its_reporting_window_opens(self):
        later = replace(
            self.listings[SOUNDBAR], ends_at=self.now + timedelta(hours=20)
        )
        with temporary_database() as database:
            first = self._run_with_database(database, [later], within_hours=6)
            inside = replace(later, ends_at=self.now + timedelta(hours=4))
            second = self._run_with_database(database, [inside], within_hours=6)

        self.assertEqual(first.listings_scored, 0)
        self.assertTrue(second.candidates)
        self.assertFalse(any(candidate.change.is_new for candidate in second.candidates))

    def _run(self, listings, within_hours=None):
        with temporary_database() as database:
            return self._run_with_database(database, listings, within_hours)

    def _run_with_database(self, database, listings, within_hours=None):
        config = replace(
            self.config,
            reports=replace(self.config.reports, closing_within_hours=within_hours),
        )
        return analyze_listings(
            listings,
            config,
            observations=ObservationStore(database),
            decisions=LogisticsDecisionStore(database),
            now=self.now,
        )


class OutcomeAwareInterestTests(unittest.TestCase):
    """A confirmed purchase retires only the finite want it satisfies."""

    def setUp(self):
        self.config = example_config()
        self.listing = example_listings()[SOUNDBAR]
        self.soundbar = next(
            rule for rule in self.config.interests if rule.name == "soundbar"
        )
        self.reference = InterestRef(
            self.soundbar.interest_id, self.soundbar.name
        )

    def test_a_confirmed_win_retires_the_interest_but_not_general_discovery(self):
        with temporary_database() as database:
            store = self._watchlist(database)
            store.save(self._won())

            result = self._run(database, store)

        self.assertFalse(
            any(
                candidate.category == CandidateCategory.WANTED
                and candidate.rule_id == self.soundbar.interest_id
                for candidate in result.candidates
            )
        )
        self.assertTrue(
            any(
                candidate.category == CandidateCategory.ANOMALY
                for candidate in result.candidates
            )
        )
        status = next(
            item
            for item in result.interest_progress
            if item.interest.interest_id == self.soundbar.interest_id
        )
        self.assertTrue(status.is_retired)

    def test_a_win_without_an_explicit_allocation_keeps_the_interest_active(self):
        with temporary_database() as database:
            store = self._watchlist(database)
            store.save(
                replace(
                    self._won(),
                    fulfilled_interests=(),
                    fulfillment_reviewed=False,
                )
            )

            result = self._run(database, store)

        self.assertTrue(
            any(
                candidate.rule_id == self.soundbar.interest_id
                for candidate in result.candidates
            )
        )
        self.assertEqual(result.unreviewed_wins, 1)

    def test_a_reported_lot_remembers_every_interest_match_not_the_anomaly(self):
        second = replace(
            self.soundbar,
            interest_id="home-audio",
            name="home audio",
        )
        config = replace(self.config, interests=(self.soundbar, second))
        with temporary_database() as database:
            store = self._watchlist(database)
            self._run(database, store, config=config)
            followed = store.get(self.listing.source, self.listing.listing_id)

        self.assertEqual(
            {reference.interest_id for reference in followed.matched_interests},
            {self.soundbar.interest_id, second.interest_id},
        )
        self.assertNotIn("retail-ratio", {
            reference.interest_id for reference in followed.matched_interests
        })

    def test_increasing_wanted_reactivates_the_rule_on_the_next_run(self):
        config = replace(
            self.config,
            interests=tuple(
                replace(rule, wanted=2) if rule == self.soundbar else rule
                for rule in self.config.interests
            ),
        )
        with temporary_database() as database:
            store = self._watchlist(database)
            store.save(self._won())

            result = self._run(database, store, config=config)

        self.assertTrue(
            any(
                candidate.rule_id == self.soundbar.interest_id
                for candidate in result.candidates
            )
        )
        status = next(
            item
            for item in result.interest_progress
            if item.interest.interest_id == self.soundbar.interest_id
        )
        self.assertEqual(status.remaining, 1)

    def _won(self) -> WatchedItem:
        return WatchedItem(
            source=self.listing.source,
            listing_id="won-lot",
            matched_interests=(self.reference,),
            fulfilled_interests=(self.reference,),
            verdict=Verdict.WON,
        )

    def _watchlist(self, database) -> WatchlistStore:
        return WatchlistStore(Path(database.path).parent / "watchlist.json")

    def _run(self, database, store, *, config=None):
        return analyze_listings(
            [self.listing],
            config or self.config,
            observations=ObservationStore(database),
            decisions=LogisticsDecisionStore(database),
            watchlist=store,
        )
