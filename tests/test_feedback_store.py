"""Private feedback events preserve decisions without leaking watchlist details."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from auction_lens.feedback.model import (
    FeedbackLabel,
    FeedbackTargetKind,
)
from auction_lens.feedback.record import (
    clear_feedback,
    configuration_sha256,
    infer_feedback_target,
    record_feedback,
)
from auction_lens.feedback.store import FeedbackStore
from auction_lens.listings.conditions import ConditionTag, Tag
from auction_lens.matching.progress import InterestRef
from auction_lens.watchlist.model import PriceReading, WatchedItem
from support import temporary_directory

DIGEST = "a" * 64
NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


class FeedbackTargetTests(unittest.TestCase):
    def test_zero_matches_means_the_price_anomaly_target(self):
        target = infer_feedback_target(an_item())
        self.assertEqual(target.kind, FeedbackTargetKind.ANOMALY)
        self.assertEqual(target.target_id, "price-anomaly")

    def test_one_match_is_inferred_and_more_than_one_requires_a_choice(self):
        audio = InterestRef("audio", "Home audio")
        item = an_item(matches=(audio,))
        self.assertEqual(infer_feedback_target(item).target_id, "audio")

        item = replace(
            item,
            matched_interests=(audio, InterestRef("office", "Office audio")),
        )
        with self.assertRaisesRegex(ValueError, "more than one interest"):
            infer_feedback_target(item)
        self.assertEqual(infer_feedback_target(item, "OFFICE").name, "Office audio")
        self.assertEqual(infer_feedback_target(item, "home AUDIO").target_id, "audio")

    def test_stable_id_wins_before_an_equal_display_name(self):
        item = an_item(
            matches=(
                InterestRef("tools", "Workshop"),
                InterestRef("other", "tools"),
            )
        )
        self.assertEqual(infer_feedback_target(item, "tools").target_id, "tools")


class FeedbackStoreTests(unittest.TestCase):
    def test_every_public_label_has_the_locked_spelling(self):
        self.assertEqual(
            [label.value for label in FeedbackLabel],
            [
                "yes",
                "maybe",
                "no",
                "wrong-item",
                "too-expensive",
                "logistics-impossible",
            ],
        )

    def test_record_round_trips_version_one_with_exact_decimal_text(self):
        with temporary_directory() as directory:
            path = directory / "feedback.json"
            store = FeedbackStore(path)
            event = record_feedback(
                store,
                an_item(matches=(InterestRef("audio", "Home audio"),)),
                "too-expensive",
                DIGEST,
                note="Price crossed my line",
                recorded_at=NOW,
            )
            document = json.loads(path.read_text("utf-8"))
            (stored,) = store.events()

        self.assertEqual(stored, event)
        self.assertEqual(document["version"], 1)
        evidence = document["events"][0]["evidence"]
        self.assertEqual(evidence["all_in_cost"], "82.37")
        self.assertEqual(evidence["estimated_retail"], "199.99")
        self.assertNotIn("url", evidence)
        self.assertNotIn("photo", " ".join(evidence))
        self.assertNotIn("note", evidence)
        self.assertNotIn("profile", evidence)
        self.assertNotIn("private watchlist thought", json.dumps(evidence))

    def test_same_meaning_is_a_no_op_and_clear_is_an_append_only_tombstone(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            item = an_item(matches=(InterestRef("audio", "Home audio"),))
            first = record_feedback(store, item, "yes", DIGEST, recorded_at=NOW)
            repeated = record_feedback(
                store, item, "yes", DIGEST, recorded_at=NOW + timedelta(hours=1)
            )
            cleared = clear_feedback(
                store, item, DIGEST, recorded_at=NOW + timedelta(hours=2)
            )
            cleared_again = clear_feedback(
                store, item, DIGEST, recorded_at=NOW + timedelta(hours=3)
            )

            self.assertIsNotNone(first)
            self.assertIsNone(repeated)
            self.assertIsNotNone(cleared)
            self.assertIsNone(cleared_again)
            self.assertEqual(len(store.events()), 2)
            self.assertEqual(store.current(), ())
            self.assertEqual(store.events()[-1].action.value, "clear")
            self.assertIsNone(store.events()[-1].label)

    def test_physical_item_identity_survives_a_relisting(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            first = an_item(
                listing_id="auction-one",
                inventory_id="physical-7",
                matches=(InterestRef("audio", "Home audio"),),
            )
            relisted = replace(first, listing_id="auction-two")
            record_feedback(store, first, "maybe", DIGEST, recorded_at=NOW)
            self.assertIsNone(
                record_feedback(
                    store,
                    relisted,
                    "maybe",
                    DIGEST,
                    recorded_at=NOW + timedelta(days=1),
                )
            )
            record_feedback(
                store,
                relisted,
                "yes",
                DIGEST,
                recorded_at=NOW + timedelta(days=2),
            )

            self.assertEqual(len(store.events()), 2)
            (current,) = store.current()
            self.assertEqual(current.item_key, "synthetic/physical-7")
            self.assertEqual(current.evidence.listing_id, "auction-two")

    def test_a_naive_event_time_and_overlong_note_are_refused_before_writing(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            with self.assertRaisesRegex(ValueError, "timezone"):
                record_feedback(
                    store,
                    an_item(),
                    "no",
                    DIGEST,
                    recorded_at=datetime(2026, 1, 1),
                )
            with self.assertRaisesRegex(ValueError, "500"):
                record_feedback(store, an_item(), "no", DIGEST, note="x" * 501)
            self.assertFalse(store.path.exists())

    def test_configuration_hash_is_of_the_exact_file_bytes(self):
        with temporary_directory() as directory:
            path = directory / "settings.toml"
            path.write_bytes(b"[example]\nvalue = 1\n")
            self.assertEqual(
                configuration_sha256(path),
                "3907f2deaf9119a5580014c776e0c8cde3ebfe04e5b18a4b49e0274f94f274ba",
            )

    def test_malformed_or_future_files_are_refused_without_rewriting_them(self):
        documents = (
            [],
            {"version": True, "events": []},
            {"version": 1.0, "events": []},
            {"version": 2, "events": []},
            {"version": 1, "events": "not a list"},
        )
        for number, document in enumerate(documents):
            with self.subTest(document=document):
                with temporary_directory() as directory:
                    path = directory / f"feedback-{number}.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    before = path.read_bytes()

                    with self.assertRaises(ValueError):
                        record_feedback(
                            FeedbackStore(path),
                            an_item(),
                            "no",
                            DIGEST,
                            recorded_at=NOW,
                        )

                    self.assertEqual(path.read_bytes(), before)


def an_item(
    *,
    listing_id: str = "auction-1",
    inventory_id: str = "physical-1",
    matches: tuple[InterestRef, ...] = (),
    total: str = "82.37",
    retail: str = "199.99",
) -> WatchedItem:
    return WatchedItem(
        source="synthetic",
        listing_id=listing_id,
        inventory_id=inventory_id,
        title="Synthetic speaker",
        url="https://example.invalid/private-link",
        photo_urls=("https://example.invalid/private-photo",),
        estimated_retail=Decimal(retail),
        conditions=(ConditionTag("functional", "Functional", Tag.GREEN),),
        quality_rating=5,
        matched_interests=matches,
        note="private watchlist thought",
        readings=(
            PriceReading(
                scanned_at=NOW,
                current_bid=Decimal("70"),
                total_cost=Decimal(total),
                listing_id=listing_id,
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
