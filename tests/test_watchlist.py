"""Following lots between runs, and reading the file that remembers them."""

from __future__ import annotations

import json
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from auction_lens.grading import read_grade
from auction_lens.models import PriceReading, Verdict, WatchedItem
from auction_lens.pipeline import analyze_listings
from auction_lens.reporting import render_watchlist
from auction_lens.storage import LogisticsDecisionStore, ObservationStore, WatchlistStore
from support import (
    SOUNDBAR,
    example_config,
    example_listings,
    temporary_database,
    temporary_directory,
)

AN_HOUR = timedelta(hours=1)


@contextmanager
def _temporary_watchlist() -> Iterator[WatchlistStore]:
    """A store whose file disappears when the test finishes with it."""
    with temporary_directory() as directory:
        yield WatchlistStore(directory / "watchlist.json")


class WatchedItemTests(unittest.TestCase):
    def test_the_written_word_becomes_the_verdict_it_names(self):
        item = WatchedItem(source="nellis", listing_id="1", verdict="hunting")
        self.assertEqual(item.verdict, Verdict.HUNTING)

    def test_a_verdict_nobody_defined_is_refused(self):
        with self.assertRaisesRegex(ValueError, "verdict must be one of"):
            WatchedItem(source="nellis", listing_id="1", verdict="maybe-ish")

    def test_only_the_tags_that_are_not_green_count_as_concerns(self):
        grade = read_grade({"condition": "Used", "damage": "None"})
        item = WatchedItem(source="nellis", listing_id="1", conditions=grade.tags)
        self.assertEqual([tag.label for tag in item.concerns], ["Used"])

    def test_headroom_goes_negative_once_a_lot_costs_more_than_it_is_worth(self):
        # A $75 bid costs $86.25 all in, against an estimate of $60.
        item = _followed(my_estimate="60", bids=("50", "75"))
        self.assertEqual(item.headroom, Decimal("-26.25"))

    def test_a_lot_seen_once_has_travelled_nowhere_worth_reporting(self):
        self.assertIsNone(_followed(bids=("50",)).movement)
        self.assertEqual(_followed(bids=("50", "62")).movement, Decimal("12"))


class WatchlistStoreTests(unittest.TestCase):
    def test_an_absent_file_reads_as_an_empty_watchlist(self):
        with temporary_directory() as directory:
            self.assertEqual(WatchlistStore(directory / "none.json").items(), ())

    def test_a_saved_lot_round_trips_through_the_file(self):
        with temporary_directory() as directory:
            store = WatchlistStore(directory / "watchlist.json")
            store.save(_followed(my_estimate="60", verdict="hunting"))
            (stored,) = store.items()
        self.assertEqual(stored.uid, "nellis:sb-1")
        self.assertEqual(stored.my_estimate, Decimal("60"))
        self.assertEqual(stored.verdict, Verdict.HUNTING)

    def test_dropping_a_lot_says_whether_there_was_one_to_drop(self):
        with temporary_directory() as directory:
            store = WatchlistStore(directory / "watchlist.json")
            store.save(_followed())
            self.assertTrue(store.drop("nellis", "sb-1"))
            self.assertFalse(store.drop("nellis", "sb-1"))

    def test_an_unreadable_entry_names_itself_rather_than_the_whole_file(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "uid": "nellis:sb-1",
                                "source": "nellis",
                                "listing_id": "sb-1",
                                "my_estimate": "not money",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "nellis:sb-1: my_estimate"):
                WatchlistStore(path).items()


class RunRecordingTests(unittest.TestCase):
    """What a run adds to the file, and what it must never take away."""

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_a_run_records_one_reading_for_every_reported_lot(self):
        with self._store() as store:
            result = self._run(self.listings, store)
            items = store.items()

        self.assertEqual(result.lots_followed, 2)
        self.assertEqual(
            {item.uid for item in items},
            {"nellis:synthetic-001", "nellis:synthetic-002"},
        )
        self.assertTrue(all(len(item.readings) == 1 for item in items))

    def test_a_lot_matching_two_rules_still_leaves_one_reading(self):
        soundbar = self.listings[SOUNDBAR]
        with self._store() as store:
            result = self._run([soundbar], store)
            (item,) = store.items()

        # The soundbar is both a wanted match and a retail-ratio anomaly.
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(len(item.readings), 1)

    def test_scanning_hourly_leaves_an_hourly_trail(self):
        with self._store() as store:
            self._run(self.listings, store)
            self._run(self._an_hour_later(self.listings, bid="26.00"), store)
            item = store.get("nellis", "synthetic-001")

        trail = [str(reading.current_bid) for reading in item.readings]
        self.assertEqual(trail, ["18.00", "26.00"])
        self.assertEqual(item.movement, Decimal("8.00"))

    def test_reading_the_same_scan_twice_does_not_double_the_trail(self):
        with self._store() as store:
            self._run(self.listings, store)
            repeated = self._run(self.listings, store)
            item = store.get("nellis", "synthetic-001")

        self.assertEqual(repeated.lots_followed, 0)
        self.assertEqual(len(item.readings), 1)

    def test_a_run_never_overwrites_what_a_person_wrote_down(self):
        with self._store() as store:
            self._run(self.listings, store)
            store.save(
                replace(
                    store.get("nellis", "synthetic-001"),
                    my_estimate=Decimal("60"),
                    verdict=Verdict.HUNTING,
                    note="worth it under 40",
                )
            )
            self._run(self._an_hour_later(self.listings, bid="26.00"), store)
            item = store.get("nellis", "synthetic-001")

        self.assertEqual(item.my_estimate, Decimal("60"))
        self.assertEqual(item.verdict, Verdict.HUNTING)
        self.assertEqual(item.note, "worth it under 40")
        self.assertEqual(len(item.readings), 2)

    def _run(self, listings, store):
        with temporary_database() as database:
            return analyze_listings(
                listings,
                self.config,
                observations=ObservationStore(database),
                decisions=LogisticsDecisionStore(database),
                watchlist=store,
            )

    def _an_hour_later(self, listings, *, bid: str) -> list:
        return [
            replace(
                listing,
                observed_at=listing.observed_at + AN_HOUR,
                current_bid=Decimal(bid),
            )
            for listing in listings
        ]

    def _store(self):
        return _temporary_watchlist()


class WatchlistRenderingTests(unittest.TestCase):
    def test_an_empty_watchlist_says_so_instead_of_printing_nothing(self):
        self.assertIn("empty", render_watchlist(()))

    def test_the_lots_being_chased_are_printed_before_the_ones_passed_on(self):
        chased = _followed(listing_id="chased", title="Chased", verdict="hunting")
        passed = _followed(listing_id="passed", title="Passed", verdict="passed")
        text = render_watchlist((passed, chased))
        self.assertLess(text.index("Chased"), text.index("Passed"))

    def test_a_lot_shows_its_tag_stars_headroom_and_trail(self):
        item = _followed(my_estimate="60", rating=3, verdict="hunting", bids=("18", "26"))
        text = render_watchlist((item,))
        self.assertIn("[HUNTING] ***..", text)
        self.assertIn("My estimate $60", text)
        self.assertIn("Headroom $30.10", text)
        self.assertIn("+$8 over 2 looks", text)

    def test_a_loss_reads_as_a_negative_amount_not_a_stray_minus_sign(self):
        item = _followed(my_estimate="10", bids=("18",))
        self.assertIn("Headroom -$10.70", render_watchlist((item,)))


def _followed(
    *,
    listing_id: str = "sb-1",
    title: str = "Example Sound Bar",
    my_estimate: str | None = None,
    rating: int | None = None,
    verdict: str = "watching",
    bids: tuple[str, ...] = (),
) -> WatchedItem:
    """One followed lot, with a price trail described as a list of bids."""
    listing = example_listings()[SOUNDBAR]
    return WatchedItem(
        source="nellis",
        listing_id=listing_id,
        title=title,
        url=listing.url,
        estimated_retail=listing.estimated_retail,
        my_estimate=None if my_estimate is None else Decimal(my_estimate),
        verdict=verdict,
        quality_rating=rating,
        readings=tuple(
            PriceReading(
                scanned_at=listing.observed_at + index * AN_HOUR,
                current_bid=Decimal(bid),
                total_cost=Decimal(bid) * Decimal("1.15"),
            )
            for index, bid in enumerate(bids)
        ),
    )


if __name__ == "__main__":
    unittest.main()
