"""One analysis run, from listings to candidates."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from auction_lens.history.logistics import LogisticsDecisionStore
from auction_lens.history.observations import ObservationStore
from auction_lens.matching.analyze import (
    analyze_listings,
    select_automatic_candidates,
)
from auction_lens.matching.evaluate import estimate_total_cost, evaluate
from auction_lens.matching.judge import VettingOutcome
from auction_lens.matching.model import CandidateCategory
from auction_lens.matching.progress import InterestRef
from auction_lens.pricing.closing_history import BASIS
from auction_lens.pricing.model import ValuationBand, ValuationSummary
from auction_lens.pricing.value import ValuationEngine
from auction_lens.reports.findings import build_report
from auction_lens.reports.text import render_text
from auction_lens.watchlist.model import Verdict, WatchedItem
from auction_lens.watchlist.store import WatchlistStore
from support import (
    REPORT_ZONE,
    SOUNDBAR,
    example_config,
    example_listings,
    shortly_before_close,
    temporary_database,
)


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
            first = self._run(database, self.listings)
            second = self._run(database, self.listings)
        self.assertFalse(any(item.change.is_new for item in second.candidates))
        first_by_rule = {
            (item.listing.listing_id, item.rule_id): item for item in first.candidates
        }
        for candidate in second.candidates:
            first_seen = first_by_rule[(candidate.listing.listing_id, candidate.rule_id)]
            self.assertEqual(candidate.score, first_seen.score)
        self.assertTrue(
            any(
                first_by_rule[(item.listing.listing_id, item.rule_id)].priority
                > item.priority
                for item in second.candidates
            )
        )

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
            now=shortly_before_close(),
        )


class AutomaticCandidateSelectionTests(unittest.TestCase):
    def setUp(self):
        found = evaluate(
            example_listings()[SOUNDBAR], example_config(), now=shortly_before_close()
        )
        self.wanted = next(
            item for item in found if item.category is CandidateCategory.WANTED
        )
        self.anomaly = next(
            item for item in found if item.category is CandidateCategory.ANOMALY
        )

    def test_judge_refusals_remain_diagnostic_only(self):
        refused = replace(self.wanted, set_aside=True)

        selection = select_automatic_candidates(
            [refused, self.anomaly],
            VettingOutcome(candidates=(refused, self.anomaly)),
        )

        self.assertEqual(selection.candidates, (self.anomaly,))
        self.assertEqual(selection.set_aside_matches, 1)
        self.assertEqual(selection.unvetted_matches, 0)

    def test_a_judge_outage_fails_closed_only_for_wanted_matches(self):
        selection = select_automatic_candidates(
            [self.wanted, self.anomaly],
            VettingOutcome(
                candidates=(self.wanted, self.anomaly),
                unavailable="synthetic outage",
            ),
        )

        self.assertEqual(selection.candidates, (self.anomaly,))
        self.assertEqual(selection.set_aside_matches, 0)
        self.assertEqual(selection.unvetted_matches, 1)


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
                now=shortly_before_close(),
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
                now=shortly_before_close(),
            )


class ObservedCloseGateTests(unittest.TestCase):
    """A bargain claim can be contradicted by what the thing really fetches.

    A price anomaly rests entirely on stated retail, which is the seller's own
    number. Where the same product has closed here before, that is a better
    witness: one real lot scored 89 as 8% of a claimed $84 for a bookshelf that
    had twice closed at $2 and $3.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _engine(self, low, high, basis=BASIS):
        """An engine that reports one band, so the test states the evidence."""

        class OneBand:
            def value(self, listing):
                return ValuationSummary(
                    bands=(
                        ValuationBand(
                            basis=basis,
                            low=Decimal(low),
                            typical=Decimal(low),
                            high=Decimal(high),
                            source_count=1,
                            sample_size=2,
                        ),
                    )
                )

        return OneBand()

    def _anomalies(self, bid, engine):
        """Every anomaly surviving a run where the lot is bid up to `bid`."""
        priced = [
            replace(listing, current_bid=Decimal(bid)) for listing in self.listings
        ]
        with temporary_database() as database:
            result = analyze_listings(
                priced,
                self.config,
                observations=ObservationStore(database),
                decisions=LogisticsDecisionStore(database),
                valuation_engine=engine,
                now=shortly_before_close(),
            )
        return [
            item
            for item in result.all_candidates
            if item.category == CandidateCategory.ANOMALY
        ]

    def test_an_anomaly_bid_past_every_observed_close_is_dropped(self):
        self.assertEqual(self._anomalies("30", self._engine("5", "24")), [])

    def test_an_anomaly_still_under_the_ceiling_survives(self):
        self.assertTrue(self._anomalies("1", self._engine("5", "24")))

    def test_the_ceiling_itself_is_not_past_it(self):
        """Equal is not greater; a lot at the best observed price still counts."""
        self.assertTrue(self._anomalies("24", self._engine("5", "24")))

    def test_evidence_of_another_kind_does_not_gate_anything(self):
        """Only observed closes here are a witness to what this fetches."""
        surviving = self._anomalies("30", self._engine("5", "24", basis="used_sold"))
        self.assertTrue(surviving)

    def test_a_wanted_match_is_never_dropped_by_price_evidence(self):
        """Its claim is that somebody asked for this, which a price cannot deny."""
        priced = [
            replace(listing, current_bid=Decimal("30")) for listing in self.listings
        ]
        with temporary_database() as database:
            result = analyze_listings(
                priced,
                self.config,
                observations=ObservationStore(database),
                decisions=LogisticsDecisionStore(database),
                valuation_engine=self._engine("1", "2"),
                now=shortly_before_close(),
            )
        wanted = [
            item
            for item in result.all_candidates
            if item.category == CandidateCategory.WANTED
        ]
        self.assertTrue(wanted, "a wanted match should survive cheap comparables")

    def test_without_an_engine_nothing_is_gated(self):
        """The gate can only ever remove a lot it has evidence against."""
        self.assertTrue(self._anomalies("30", None))


class LikelyTotalTests(unittest.TestCase):
    """What winning a lot is likely to cost, beside what it costs right now."""

    def setUp(self):
        self.config = example_config()
        self.listing = example_listings()[SOUNDBAR]

    def _candidates(self, band):
        class OneBand:
            def value(self, listing):
                return ValuationSummary(bands=(band,) if band else ())

        with temporary_database() as database:
            result = analyze_listings(
                [self.listing],
                self.config,
                observations=ObservationStore(database),
                decisions=LogisticsDecisionStore(database),
                valuation_engine=OneBand(),
                now=shortly_before_close(),
            )
        return result.all_candidates

    def _band(self, basis, low, typical, high, sample_size=40):
        return ValuationBand(
            basis=basis,
            low=Decimal(low),
            typical=Decimal(typical),
            high=Decimal(high),
            source_count=1,
            sample_size=sample_size,
        )

    def _total_at(self, bid):
        moved = replace(self.listing, current_bid=Decimal(bid))
        return estimate_total_cost(moved, self.config.economics)

    def test_the_likely_total_is_the_total_at_the_likely_bids(self):
        """Premium, tax and fee are charged on the bid it closes at, not today's."""
        candidate = self._candidates(self._band(BASIS, "40", "55", "80"))[0]
        likely = candidate.likely_total
        self.assertEqual(
            (likely.low, likely.typical, likely.high),
            (self._total_at("40"), self._total_at("55"), self._total_at("80")),
        )
        self.assertGreater(likely.typical, candidate.total_cost)

    def test_the_report_names_what_the_range_is_made_of(self):
        candidates = self._candidates(self._band(BASIS, "40", "55", "80", 3))
        text = render_text(build_report(list(candidates), REPORT_ZONE))
        self.assertIn(
            f"Likely total: ${self._total_at('55'):,.0f} "
            f"(${self._total_at('40'):,.0f}-${self._total_at('80'):,.0f}, "
            "this product's 3 past closes)",
            text,
        )
        self.assertIn(f"Total now: ${candidates[0].total_cost}", text)

    def test_a_band_on_another_basis_is_declined_rather_than_relabelled(self):
        """A card states observed closes or admits it has none.

        A band scaled from stated retail was once reported here as the middle
        half of lots at the same stage, which read as evidence and was not.
        Declining by basis rather than renaming the sentence is what stops a
        later source inheriting it by accident.
        """
        band = self._band("similar_lots_close", "40", "55", "80")
        text = render_text(build_report(list(self._candidates(band)), REPORT_ZONE))
        self.assertIn("Likely total: not enough closes recorded yet", text)
        self.assertNotIn("at this stage", text)

    def test_the_product_s_own_closes_are_named_as_such(self):
        candidates = self._candidates(self._band(BASIS, "40", "55", "80", 3))
        text = render_text(build_report(list(candidates), REPORT_ZONE))
        self.assertIn("this product's 3 past closes", text)

    def test_a_lot_with_no_history_says_so_rather_than_repeating_the_bid(self):
        candidates = self._candidates(None)
        text = render_text(build_report(list(candidates), REPORT_ZONE))
        self.assertIn("Likely total: not enough closes recorded yet", text)

    def test_without_a_price_source_nothing_is_claimed_about_history(self):
        with temporary_database() as database:
            result = analyze_listings(
                [self.listing],
                self.config,
                observations=ObservationStore(database),
                decisions=LogisticsDecisionStore(database),
                now=shortly_before_close(),
            )
        text = render_text(build_report(list(result.all_candidates), REPORT_ZONE))
        self.assertNotIn("Likely total", text)


class StillOpenTests(unittest.TestCase):
    """A report is a list of things that can still be bid on.

    That is the whole of the test now. A report once also cut lots closing
    further out than a configured window, which sounded like a preference and
    behaved like a clock: the same setting hid half a day's lots from a morning
    run and none at all from an evening one. Closing time is a ranking signal,
    so it is ranked instead.
    """

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
        self.assertEqual(result.lots_already_closed, len(closed))
        self.assertFalse(result.candidates)

    def test_a_lot_closing_far_out_is_read_and_counted_not_set_aside(self):
        # The removed window set these aside before scoring and said nothing.
        # They are still read, still observed, and still counted; what a later
        # change narrowed is only when a want earns its card, which is on its
        # closing day. A month out that is no card yet, and the count says so.
        far = [
            replace(listing, ends_at=self.now + timedelta(days=30))
            for listing in self.listings
        ]
        result = self._run(far)
        self.assertEqual(result.listings_scored, len(far))
        self.assertEqual(result.lots_already_closed, 0)
        self.assertFalse(result.candidates)
        self.assertEqual(result.lots_closing_later, result.listings_scored)

    def test_a_lot_stating_no_closing_time_is_kept_rather_than_guessed_about(self):
        # Silence is not a reason to hide something the operator asked for.
        silent = [replace(listing, ends_at=None) for listing in self.listings]
        result = self._run(silent)
        self.assertEqual(result.listings_scored, len(silent))
        self.assertEqual(result.lots_already_closed, 0)

    def test_a_closed_lot_is_not_confused_with_another_provider_s_listing(self):
        # Both counts are subtracted from the same total, so an error in one
        # would silently show up as the other.
        stranger = replace(self.listings[SOUNDBAR], source="other-provider")
        closed = replace(self.listings[1], ends_at=self.now - timedelta(minutes=1))

        result = self._run([self.listings[SOUNDBAR], stranger, closed])

        self.assertEqual(result.listings_read, 3)
        self.assertEqual(result.listings_scored, 1)
        self.assertEqual(result.lots_already_closed, 1)
        self.assertEqual(result.listings_from_other_providers, 1)

    def test_a_lot_is_observed_even_on_the_run_that_sets_it_aside(self):
        # Being shown a lot is a historical fact. Whether the lot is worth
        # scoring today must not decide whether that fact was recorded, or a
        # relisting would look new when it is not.
        # Two hours, which stays inside the reading's own day in the example
        # config's zone. Four crossed midnight there and held the lot back for
        # closing "tomorrow", which is a fact about this fixture's clock.
        open_lot = replace(self.listings[SOUNDBAR], ends_at=self.now + timedelta(hours=2))
        closed = replace(open_lot, ends_at=self.now - timedelta(minutes=1))
        with temporary_database() as database:
            first = self._run_with_database(database, [closed])
            second = self._run_with_database(database, [open_lot])

        self.assertEqual(first.listings_scored, 0)
        self.assertTrue(second.candidates)
        self.assertFalse(any(candidate.change.is_new for candidate in second.candidates))

    def _run(self, listings):
        with temporary_database() as database:
            return self._run_with_database(database, listings)

    def _run_with_database(self, database, listings):
        return analyze_listings(
            listings,
            self.config,
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
            now=shortly_before_close(),
        )


if __name__ == "__main__":
    unittest.main()
