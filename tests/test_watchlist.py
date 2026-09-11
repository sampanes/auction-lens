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
from auction_lens.models import InterestRef, PriceReading, Verdict, WatchedItem
from auction_lens.pipeline import analyze_listings
from auction_lens.reporting import render_watchlist, render_watchlist_html
from auction_lens.storage import (
    FollowedListing,
    LogisticsDecisionStore,
    ObservationStore,
    WatchlistStore,
)
from support import (
    SOUNDBAR,
    example_config,
    example_listings,
    temporary_database,
    temporary_directory,
)

AN_HOUR = timedelta(hours=1)
ESCAPE = chr(27)


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

    def test_a_saved_allocation_is_itself_proof_that_fulfillment_was_reviewed(self):
        audio = InterestRef("audio", "Home audio")

        item = WatchedItem(
            source="synthetic",
            listing_id="one",
            matched_interests=(audio,),
            fulfilled_interests=(audio,),
        )

        self.assertTrue(item.fulfillment_reviewed)

    def test_fulfillment_reviewed_is_a_real_boolean_not_truthy_text(self):
        with self.assertRaisesRegex(
            ValueError, "fulfillment_reviewed must be true or false"
        ):
            WatchedItem(
                source="synthetic",
                listing_id="one",
                fulfillment_reviewed="false",
            )


class WatchlistStoreTests(unittest.TestCase):
    def test_an_absent_file_reads_as_an_empty_watchlist(self):
        with temporary_directory() as directory:
            self.assertEqual(WatchlistStore(directory / "none.json").items(), ())

    def test_a_saved_lot_round_trips_through_the_file(self):
        with temporary_directory() as directory:
            store = WatchlistStore(directory / "watchlist.json")
            store.save(
                replace(
                    _followed(my_estimate="60", verdict="hunting"),
                    matched_interests=(InterestRef("audio", "Home audio"),),
                    fulfilled_interests=(InterestRef("audio", "Earlier audio name"),),
                )
            )
            (stored,) = store.items()
            document = json.loads((directory / "watchlist.json").read_text("utf-8"))
        self.assertEqual(stored.item_key, "nellis/sb-1")
        self.assertEqual(stored.my_estimate, Decimal("60"))
        self.assertEqual(stored.verdict, Verdict.HUNTING)
        self.assertEqual(stored.matched_interests[0].interest_id, "audio")
        self.assertEqual(stored.fulfilled_interests[0].name, "Earlier audio name")
        self.assertTrue(stored.fulfillment_reviewed)
        self.assertEqual(document["version"], 2)
        self.assertIs(document["items"][0]["fulfillment_reviewed"], True)
        self.assertEqual(
            document["items"][0]["matched_interests"],
            [{"id": "audio", "name": "Home audio"}],
        )

    def test_a_version_one_file_upgrades_without_losing_history_or_opinions(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "source": "nellis",
                                "listing_id": "legacy-auction-2",
                                "inventory_id": "legacy-item-1",
                                "title": "Example legacy lot",
                                "url": "https://example.invalid/legacy-auction-2",
                                "estimated_retail": "125.00",
                                "my_estimate": "42.50",
                                "verdict": "won",
                                "note": "keep this human decision",
                                "readings": [
                                    {
                                        "scanned_at": "2026-09-01T12:00:00+00:00",
                                        "current_bid": "10.00",
                                        "total_cost": "11.50",
                                        "bid_count": 1,
                                        "listing_id": "legacy-auction-1",
                                    },
                                    {
                                        "scanned_at": "2026-09-02T12:00:00+00:00",
                                        "current_bid": "20.00",
                                        "total_cost": "23.00",
                                        "bid_count": 3,
                                        "listing_id": "legacy-auction-2",
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = WatchlistStore(path)
            (legacy,) = store.items()
            store.save(legacy)
            (upgraded,) = store.items()
            document = json.loads(path.read_text("utf-8"))

        self.assertEqual(upgraded, legacy)
        self.assertEqual(upgraded.item_key, "nellis/legacy-item-1")
        self.assertEqual(upgraded.auctions_seen, 2)
        self.assertEqual(upgraded.my_estimate, Decimal("42.50"))
        self.assertEqual(upgraded.verdict, Verdict.WON)
        self.assertEqual(upgraded.note, "keep this human decision")
        self.assertEqual(upgraded.matched_interests, ())
        self.assertEqual(upgraded.fulfilled_interests, ())
        self.assertFalse(upgraded.fulfillment_reviewed)
        self.assertEqual(document["version"], 2)
        self.assertIs(document["items"][0]["fulfillment_reviewed"], False)

    def test_an_allocated_version_two_entry_without_the_new_flag_is_reviewed(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            reference = {"id": "audio", "name": "Home audio"}
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": [
                            {
                                "source": "synthetic",
                                "listing_id": "one",
                                "matched_interests": [reference],
                                "fulfilled_interests": [reference],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            (item,) = WatchlistStore(path).items()

        self.assertTrue(item.fulfillment_reviewed)

    def test_a_non_boolean_fulfillment_review_is_refused_with_its_field_name(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": [
                            {
                                "uid": "synthetic:one",
                                "source": "synthetic",
                                "listing_id": "one",
                                "fulfillment_reviewed": "yes",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "synthetic:one: fulfillment_reviewed must be true or false",
            ):
                WatchlistStore(path).items()

    def test_dropping_a_lot_says_whether_there_was_one_to_drop(self):
        with temporary_directory() as directory:
            store = WatchlistStore(directory / "watchlist.json")
            store.save(_followed())
            self.assertTrue(store.drop("nellis", "sb-1"))
            self.assertFalse(store.drop("nellis", "sb-1"))

    def test_an_existing_non_object_is_refused_instead_of_treated_as_empty(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "watchlist must be an object"):
                WatchlistStore(path).save(_followed())

            self.assertEqual(path.read_text("utf-8"), "[]")

    def test_a_future_version_is_refused_without_rewriting_the_file(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            original = json.dumps({"version": 99, "items": [{"future": "field"}]})
            path.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported watchlist version 99"):
                WatchlistStore(path).save(_followed())

            self.assertEqual(path.read_text("utf-8"), original)

    def test_duplicate_physical_items_are_refused_before_a_write(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            original = json.dumps(
                {
                    "version": 2,
                    "items": [
                        {
                            "source": "synthetic",
                            "listing_id": "auction-one",
                            "inventory_id": "physical-one",
                        },
                        {
                            "source": "synthetic",
                            "listing_id": "auction-two",
                            "inventory_id": "physical-one",
                        },
                    ],
                }
            )
            path.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError, "duplicate watchlist identity synthetic/physical-one"
            ):
                WatchlistStore(path).save(_followed())

            self.assertEqual(path.read_text("utf-8"), original)

    def test_one_watch_key_cannot_alias_two_physical_items(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": [
                            {
                                "source": "synthetic",
                                "listing_id": "shared-auction",
                                "inventory_id": "physical-one",
                            },
                            {
                                "source": "synthetic",
                                "listing_id": "shared-auction",
                                "inventory_id": "physical-two",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError,
                "watch key synthetic/shared-auction refers to both",
            ):
                WatchlistStore(path).items()

    def test_an_existing_document_requires_an_items_list(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text(
                json.dumps({"version": 2, "items": {"not": "a list"}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "items must be a list"):
                WatchlistStore(path).items()

    def test_an_unreadable_entry_names_itself_rather_than_the_whole_file(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "uid": "nellis/sb-1",
                                "source": "nellis",
                                "listing_id": "sb-1",
                                "my_estimate": "not money",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "nellis/sb-1: my_estimate"):
                WatchlistStore(path).items()

    def test_an_invalid_interest_collection_names_the_item_and_field(self):
        with temporary_directory() as directory:
            path = directory / "watchlist.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": [
                            {
                                "uid": "nellis/sb-1",
                                "source": "nellis",
                                "listing_id": "sb-1",
                                "matched_interests": {"id": "audio", "name": "Audio"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "nellis/sb-1: matched_interests must be a list"
            ):
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
            {item.item_key for item in items},
            {"nellis/synthetic-001", "nellis/synthetic-002"},
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
        self.assertEqual(
            item.matched_interests, (InterestRef("soundbar", "soundbar"),)
        )

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
            original = store.get("nellis", "synthetic-001")
            fulfilled = original.matched_interests[:1]
            store.save(
                replace(
                    original,
                    my_estimate=Decimal("60"),
                    verdict=Verdict.HUNTING,
                    note="worth it under 40",
                    fulfilled_interests=fulfilled,
                )
            )
            self._run(self._an_hour_later(self.listings, bid="26.00"), store)
            item = store.get("nellis", "synthetic-001")

        self.assertEqual(item.my_estimate, Decimal("60"))
        self.assertEqual(item.verdict, Verdict.HUNTING)
        self.assertEqual(item.note, "worth it under 40")
        self.assertEqual(item.fulfilled_interests, fulfilled)
        self.assertTrue(item.fulfillment_reviewed)
        self.assertEqual(len(item.readings), 2)

    def test_a_run_preserves_a_review_that_says_the_purchase_fulfilled_none(self):
        with self._store() as store:
            self._run(self.listings, store)
            original = store.get("nellis", "synthetic-001")
            store.save(
                replace(
                    original,
                    verdict=Verdict.WON,
                    fulfillment_reviewed=True,
                )
            )

            self._run(self._an_hour_later(self.listings, bid="26.00"), store)
            item = store.get("nellis", "synthetic-001")

        self.assertTrue(item.fulfillment_reviewed)
        self.assertEqual(item.fulfilled_interests, ())
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


class RelistingTests(unittest.TestCase):
    """A lot that does not sell comes back under a new auction id."""

    def _seen(self, store, *, listing_id: str, bid: str, hours: int):
        listing = replace(
            example_listings()[SOUNDBAR],
            listing_id=listing_id,
            inventory_id="INV-77",
            current_bid=Decimal(bid),
            observed_at=example_listings()[SOUNDBAR].observed_at + hours * AN_HOUR,
        )
        store.record(
            [FollowedListing(listing, Decimal(bid) * Decimal("1.15"))]
        )

    def test_one_item_relisted_keeps_a_single_trail(self):
        with _temporary_watchlist() as store:
            self._seen(store, listing_id="auction-1", bid="18", hours=0)
            self._seen(store, listing_id="auction-2", bid="5", hours=48)
            (item,) = store.items()

        self.assertEqual(item.item_key, "nellis/INV-77")
        self.assertEqual(len(item.readings), 2)
        self.assertEqual(item.auctions_seen, 2)

    def test_the_entry_answers_to_the_item_id_and_to_either_auction(self):
        with _temporary_watchlist() as store:
            self._seen(store, listing_id="auction-1", bid="18", hours=0)
            self._seen(store, listing_id="auction-2", bid="5", hours=48)
            for identifier in ("INV-77", "auction-1", "auction-2"):
                with self.subTest(identifier=identifier):
                    self.assertIsNotNone(store.get("nellis", identifier))
            self.assertIsNone(store.get("nellis", "never-seen"))

    def test_a_provider_with_no_item_id_still_follows_the_auction(self):
        with _temporary_watchlist() as store:
            listing = replace(example_listings()[SOUNDBAR], inventory_id="")
            store.record([FollowedListing(listing, Decimal("20"))])
            (item,) = store.items()
        self.assertEqual(item.item_key, "nellis/synthetic-001")

    def test_a_repeat_look_merges_matches_without_rewriting_human_answers(self):
        listing = replace(
            example_listings()[SOUNDBAR],
            inventory_id="INV-77",
            listing_id="auction-1",
        )
        old_audio = InterestRef("audio", "Old audio name")
        current_audio = InterestRef("audio", "Home audio")
        diy = InterestRef("diy", "DIY stock")
        with _temporary_watchlist() as store:
            store.record([FollowedListing(listing, Decimal("20"), (old_audio,))])
            store.save(
                replace(
                    store.get("nellis", "INV-77"),
                    verdict=Verdict.WON,
                    note="kept answer",
                    fulfilled_interests=(old_audio,),
                )
            )

            touched = store.record(
                [
                    FollowedListing(
                        replace(listing, title="Refreshed title"),
                        Decimal("20"),
                        (current_audio, diy),
                    )
                ]
            )
            item = store.get("nellis", "INV-77")

        self.assertEqual(touched, 1)
        self.assertEqual(len(item.readings), 1)
        self.assertEqual(item.title, "Refreshed title")
        self.assertEqual(item.matched_interests, (current_audio, diy))
        self.assertEqual(item.fulfilled_interests, (old_audio,))
        self.assertEqual(item.verdict, Verdict.WON)
        self.assertEqual(item.note, "kept answer")

    def test_the_list_says_when_a_trail_spans_more_than_one_auction(self):
        with _temporary_watchlist() as store:
            self._seen(store, listing_id="auction-1", bid="18", hours=0)
            self._seen(store, listing_id="auction-2", bid="5", hours=48)
            text = render_watchlist(store.items())
        self.assertIn("(seen in 2 auctions)", text)


class ColourTests(unittest.TestCase):
    """Colour is how a line is skimmed, never the only place the news is."""

    def _item(self):
        grade = read_grade({"condition": "Used", "missing_parts": "Unknown"})
        followed = _followed(verdict="hunting", bids=("18",))
        return replace(followed, conditions=grade.tags)

    def test_a_redirected_watchlist_carries_no_escape_sequences(self):
        text = render_watchlist((self._item(),))
        self.assertNotIn(ESCAPE, text)
        self.assertIn("[RED] Used", text)

    def test_a_terminal_gets_colour_and_still_gets_the_words(self):
        text = render_watchlist((self._item(),), colour=True)
        self.assertIn(ESCAPE + "[31m", text)
        self.assertIn(ESCAPE + "[33m", text)
        self.assertIn("[RED] Used", text)
        self.assertIn("[AMBER] Missing Parts Unknown", text)


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
        self.assertIn("Watch key: nellis/sb-1", text)
        self.assertIn("My estimate $60", text)
        self.assertIn("Headroom $30.10", text)
        self.assertIn("+$8 over 2 looks", text)

    def test_a_relisting_prints_the_auction_key_not_the_item_key(self):
        original = _followed(listing_id="auction-2", bids=("18", "5"))
        item = replace(
            original,
            inventory_id="INV-77",
            readings=(
                replace(original.readings[0], listing_id="auction-1"),
                replace(original.readings[1], listing_id="auction-2"),
            ),
        )

        text = render_watchlist((item,))
        markup = render_watchlist_html((item,))

        self.assertEqual(item.item_key, "nellis/INV-77")
        self.assertIn("Watch key: nellis/auction-2", text)
        self.assertNotIn("Watch key: nellis/INV-77", text)
        self.assertIn(
            "<strong>Watch key:</strong> <code>nellis/auction-2</code>", markup
        )
        self.assertIn("(seen in 2 auctions)", markup)

    def test_a_loss_reads_as_a_negative_amount_not_a_stray_minus_sign(self):
        item = _followed(my_estimate="10", bids=("18",))
        self.assertIn("Headroom -$10.70", render_watchlist((item,)))

    def test_html_is_phone_friendly_and_links_the_actual_lot(self):
        item = replace(
            _followed(title="Flagged monitor", bids=("18",)),
            photo_urls=("https://example.invalid/stock.jpg", "https://example.invalid/lot.jpg"),
        )
        markup = render_watchlist_html((item,))
        self.assertIn("Flagged monitor", markup)
        self.assertIn("View listing", markup)
        self.assertIn("https://example.invalid/lot.jpg", markup)

    def test_html_escapes_a_hand_written_note(self):
        item = replace(_followed(), note="<script>not markup</script>")
        markup = render_watchlist_html((item,))
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)

    def test_a_win_shows_both_why_it_matched_and_what_it_fulfilled(self):
        audio = InterestRef("audio", "Home audio")
        diy = InterestRef("diy", "DIY stock")
        item = replace(
            _followed(verdict="won"),
            matched_interests=(audio, diy),
            fulfilled_interests=(diy,),
        )

        text = render_watchlist((item,))
        markup = render_watchlist_html((item,))

        self.assertIn("Matches: Home audio [audio] | DIY stock [diy]", text)
        self.assertIn("Fulfills: DIY stock [diy]", text)
        self.assertIn("<strong>Matches:</strong> Home audio [audio]", markup)
        self.assertIn("<strong>Fulfills:</strong> DIY stock [diy]", markup)

    def test_an_unreviewed_win_points_to_the_command_that_assigns_it(self):
        item = replace(
            _followed(verdict="won"),
            matched_interests=(InterestRef("audio", "Home audio"),),
        )

        text = render_watchlist((item,))
        markup = render_watchlist_html((item,))

        self.assertIn("Fulfillment unreviewed", text)
        command = (
            "auction-lens watch --key nellis/sb-1 --verdict won "
            "--fulfills INTEREST"
        )
        self.assertIn(command, text)
        self.assertIn("<strong>Fulfillment:</strong> unreviewed", markup)
        self.assertIn(f"<code>{command}</code>", markup)

    def test_a_reviewed_win_that_fulfilled_none_says_exactly_that(self):
        item = replace(
            _followed(verdict="won"),
            matched_interests=(InterestRef("audio", "Home audio"),),
            fulfillment_reviewed=True,
        )

        text = render_watchlist((item,))
        markup = render_watchlist_html((item,))

        self.assertIn("Fulfillment reviewed: fulfills none", text)
        self.assertNotIn("unreviewed", text)
        self.assertIn("<strong>Fulfillment reviewed:</strong> fulfills none", markup)
        self.assertNotIn("unreviewed", markup)

    def test_html_escapes_the_watch_key_and_its_correction_command(self):
        item = replace(
            _followed(listing_id="lot&1", verdict="won"),
            source="<provider>",
            matched_interests=(InterestRef("audio", "Home audio"),),
        )

        markup = render_watchlist_html((item,))

        self.assertNotIn("<provider>/lot&1", markup)
        self.assertIn("&lt;provider&gt;/lot&amp;1", markup)
        self.assertIn("--key &lt;provider&gt;/lot&amp;1", markup)

    def test_a_saved_fulfillment_is_labelled_inactive_until_the_lot_is_won(self):
        audio = InterestRef("audio", "Home audio")
        item = replace(
            _followed(verdict="passed"),
            matched_interests=(audio,),
            fulfilled_interests=(audio,),
        )

        text = render_watchlist((item,))
        markup = render_watchlist_html((item,))

        self.assertIn("Fulfills (inactive until verdict is WON):", text)
        self.assertIn("Fulfills (inactive until verdict is WON):</strong>", markup)

    def test_html_escapes_recorded_interest_names_and_ids(self):
        reference = InterestRef("audio<unsafe>", "Home & <Audio>")
        item = replace(
            _followed(verdict="won"),
            matched_interests=(reference,),
            fulfilled_interests=(reference,),
        )

        markup = render_watchlist_html((item,))

        self.assertNotIn("Home & <Audio>", markup)
        self.assertIn("Home &amp; &lt;Audio&gt; [audio&lt;unsafe&gt;]", markup)


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
