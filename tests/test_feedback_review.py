"""Feedback review finds evidence boundaries but never changes configuration."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from auction_lens.config.interests import InterestRule
from auction_lens.feedback.artifacts import save_feedback_proposal
from auction_lens.feedback.model import (
    NO_CONFIG_CHANGE_NOTICE,
)
from auction_lens.feedback.record import record_feedback
from auction_lens.feedback.review import review_feedback
from auction_lens.feedback.store import FeedbackStore
from auction_lens.matching.progress import InterestRef
from auction_lens.watchlist.model import PriceReading, WatchedItem
from support import temporary_directory

DIGEST = "b" * 64
OTHER_DIGEST = "c" * 64
NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)
AUDIO = InterestRef("audio", "Home audio")


class FeedbackReviewTests(unittest.TestCase):
    def test_three_clean_price_examples_propose_exact_narrower_cost_and_ratio(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            add(store, "accepted", "yes", total="80", retail="200", minute=1)
            add(store, "possible", "maybe", total="90", retail="200", minute=2)
            add(store, "rejected", "too-expensive", total="120", retail="200", minute=3)
            rule = InterestRule(
                name="Home audio",
                interest_id="audio",
                max_total_cost=Decimal("175"),
                maximum_retail_ratio=Decimal("0.75"),
            )

            review = review_feedback(store, (rule,))

        self.assertEqual(review.notice, NO_CONFIG_CHANGE_NOTICE)
        self.assertEqual(len(review.proposals), 1)
        proposal = review.proposal
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.notice, NO_CONFIG_CHANGE_NOTICE)
        self.assertEqual(
            [(change.field, change.before, change.after) for change in proposal.changes],
            [
                ("max_total_cost", Decimal("175"), Decimal("90")),
                (
                    "maximum_retail_ratio",
                    Decimal("0.75"),
                    Decimal("0.45"),
                ),
            ],
        )
        self.assertEqual(len(proposal.evidence_ids), 3)

    def test_default_requires_three_distinct_current_items(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            add(store, "accepted", "yes", total="80", retail="200", minute=1)
            add(store, "rejected", "too-expensive", total="120", retail="200", minute=2)
            rule = InterestRule(name="Home audio", interest_id="audio")
            self.assertIsNone(review_feedback(store, (rule,)).proposal)
            self.assertIsNotNone(
                review_feedback(store, (rule,), minimum_distinct_items=2).proposal
            )

    def test_overlapping_prices_do_not_invent_a_boundary(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            add(store, "yes-low", "yes", total="80", retail="200", minute=1)
            add(store, "yes-high", "yes", total="130", retail="200", minute=2)
            add(store, "reject-mid", "too-expensive", total="120", retail="200", minute=3)
            rule = InterestRule(name="Home audio", interest_id="audio")
            review = review_feedback(store, (rule,))
        self.assertIsNone(review.proposal)

    def test_a_boundary_that_would_widen_current_rules_is_not_proposed(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            add(store, "accepted", "yes", total="90", retail="200", minute=1)
            add(store, "possible", "maybe", total="95", retail="200", minute=2)
            add(store, "rejected", "too-expensive", total="120", retail="200", minute=3)
            already_narrower = InterestRule(
                name="Home audio",
                interest_id="audio",
                max_total_cost=Decimal("80"),
                maximum_retail_ratio=Decimal("0.40"),
            )
            review = review_feedback(store, (already_narrower,))
        self.assertIsNone(review.proposal)

    def test_ratio_proposal_is_a_readable_ceiling_above_every_accepted_item(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            add(store, "accepted", "yes", total="82.37", retail="199.99", minute=1)
            add(store, "possible", "maybe", total="90", retail="210", minute=2)
            add(store, "rejected", "too-expensive", total="120", retail="200", minute=3)
            proposal = review_feedback(
                store,
                (InterestRule(name="Home audio", interest_id="audio"),),
            ).proposal

        self.assertIsNotNone(proposal)
        ratio = next(
            change.after
            for change in proposal.changes
            if change.field == "maximum_retail_ratio"
        )
        self.assertEqual(ratio, Decimal("0.43"))
        self.assertGreaterEqual(ratio, Decimal("90") / Decimal("210"))

    def test_review_uses_the_current_display_name_for_a_stable_interest_id(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            for number in range(3):
                add(store, f"wrong-{number}", "wrong-item", minute=number + 1)
            review = review_feedback(
                store,
                (InterestRule(name="Living-room sound", interest_id="audio"),),
            )

        self.assertEqual(review.patterns[0].target.name, "Living-room sound")
        self.assertIn("Living-room sound", review.patterns[0].summary)

    def test_non_price_negatives_are_patterns_only_after_three_items(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            minute = 0
            for label in ("wrong-item", "no", "logistics-impossible"):
                for suffix in range(3):
                    minute += 1
                    add(store, f"{label}-{suffix}", label, minute=minute)
            rule = InterestRule(name="Home audio", interest_id="audio")
            review = review_feedback(store, (rule,))

        self.assertEqual(
            {pattern.label.value for pattern in review.patterns},
            {"wrong-item", "no", "logistics-impossible"},
        )
        self.assertTrue(all(pattern.distinct_items == 3 for pattern in review.patterns))
        self.assertTrue(
            all(pattern.notice == NO_CONFIG_CHANGE_NOTICE for pattern in review.patterns)
        )
        self.assertEqual(review.proposals, ())

    def test_anomaly_feedback_can_never_propose_an_interest_change(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            for index, (label, cost) in enumerate(
                (("yes", "50"), ("maybe", "60"), ("too-expensive", "100"))
            ):
                item = watched(f"anomaly-{index}", total=cost, matches=())
                record_feedback(
                    store,
                    item,
                    label,
                    DIGEST,
                    recorded_at=NOW + timedelta(minutes=index),
                )
            review = review_feedback(store, ())
        self.assertEqual(review.proposals, ())

    def test_a_proposal_uses_only_evidence_from_the_current_configuration(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            add(store, "accepted", "yes", total="80", digest=DIGEST, minute=1)
            add(store, "possible", "maybe", total="90", digest=DIGEST, minute=2)
            add(
                store,
                "rejected",
                "too-expensive",
                total="120",
                digest=OTHER_DIGEST,
                minute=3,
            )
            rule = InterestRule(name="Home audio", interest_id="audio")
            self.assertIsNone(review_feedback(store, (rule,)).proposal)
            self.assertIsNone(
                review_feedback(store, (rule,), config_sha256=OTHER_DIGEST).proposal
            )
            add(
                store,
                "new-accepted",
                "yes",
                total="80",
                digest=OTHER_DIGEST,
                minute=4,
            )
            add(
                store,
                "new-possible",
                "maybe",
                total="90",
                digest=OTHER_DIGEST,
                minute=5,
            )
            proposal = review_feedback(store, (rule,), config_sha256=OTHER_DIGEST).proposal
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.config_sha256, OTHER_DIGEST)

    def test_proposal_artifact_is_deterministic_immutable_typed_and_sanitized(self):
        with temporary_directory() as directory:
            store = FeedbackStore(directory / "feedback.json")
            add(store, "accepted", "yes", total="80", minute=1)
            add(store, "possible", "maybe", total="90", minute=2)
            add(store, "rejected", "too-expensive", total="120", minute=3)
            proposal = review_feedback(
                store,
                (InterestRule(name="Home audio", interest_id="audio"),),
            ).proposal
            self.assertIsNotNone(proposal)
            proposal_dir = directory / "private" / "proposals"
            first = save_feedback_proposal(proposal, proposal_dir)
            second = save_feedback_proposal(proposal, proposal_dir)
            raw = first.read_text("utf-8")
            document = json.loads(raw)

        self.assertEqual(first, second)
        self.assertEqual(document["version"], 1)
        self.assertFalse(document["config_changed"])
        self.assertEqual(document["notice"], NO_CONFIG_CHANGE_NOTICE)
        self.assertEqual(document["config_sha256"], DIGEST)
        self.assertEqual(document["changes"][0]["before"]["type"], "unset")
        self.assertEqual(document["changes"][0]["after"]["type"], "decimal")
        self.assertEqual(document["changes"][0]["after"]["value"], "90")
        for forbidden in (
            "Synthetic sensitive title",
            "private feedback note",
            "example.invalid",
            "feedback.json",
            "@",
        ):
            self.assertNotIn(forbidden, raw)


def add(
    store: FeedbackStore,
    key: str,
    label: str,
    *,
    total: str = "80",
    retail: str = "200",
    digest: str = DIGEST,
    minute: int,
) -> None:
    record_feedback(
        store,
        watched(key, total=total, retail=retail),
        label,
        digest,
        note="private feedback note",
        recorded_at=NOW + timedelta(minutes=minute),
    )


def watched(
    key: str,
    *,
    total: str,
    retail: str = "200",
    matches: tuple[InterestRef, ...] = (AUDIO,),
) -> WatchedItem:
    return WatchedItem(
        source="synthetic",
        listing_id=f"auction-{key}",
        inventory_id=f"item-{key}",
        title="Synthetic sensitive title",
        url="https://example.invalid/do-not-copy",
        photo_urls=("https://example.invalid/photo",),
        estimated_retail=Decimal(retail),
        note="watchlist secret",
        matched_interests=matches,
        readings=(
            PriceReading(
                scanned_at=NOW,
                current_bid=Decimal(total),
                total_cost=Decimal(total),
                listing_id=f"auction-{key}",
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
