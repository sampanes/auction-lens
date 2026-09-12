"""Delivery plans compare with what each destination last accepted."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from auction_lens.models import (
    Candidate,
    CandidateCategory,
    InterestProgress,
    InterestRef,
    Listing,
    ObservationChange,
    PriceReading,
    WatchedItem,
)
from auction_lens.notifications import (
    DeliveryChannel,
    DeliveryItem,
    DeliveryRoute,
    ReportKind,
    candidate_items,
    outcome_fingerprint,
    plan_candidates,
    plan_watchlist,
    watchlist_items,
)


class CandidatePlanTests(unittest.TestCase):
    def test_unchanged_high_ranked_matches_cannot_starve_a_new_match(self):
        already_sent = _candidate("old", bid="10", score=90)
        new = _candidate("new", bid="20", score=40)

        plan = plan_candidates(
            [already_sent, new],
            {("example", "old"): "10.00"},
            limit=1,
        )

        self.assertEqual(_ids(plan.candidates), ["new"])
        self.assertEqual(plan.unchanged_matches, 1)
        self.assertEqual(plan.held_back_matches, 0)
        self.assertTrue(plan.candidates[0].change.is_new)

    def test_a_changed_price_is_compared_with_the_last_delivered_price(self):
        candidate = replace(
            _candidate("changed", bid="12"),
            change=ObservationChange(
                is_new=False,
                price_changed=False,
                previous_bid=Decimal("11"),
            ),
        )

        plan = plan_candidates(
            [candidate], {("example", "changed"): "10.00"}
        )

        change = plan.candidates[0].change
        self.assertFalse(change.is_new)
        self.assertTrue(change.price_changed)
        self.assertEqual(change.previous_bid, Decimal("10.00"))
        self.assertEqual(plan.receipts[0].revision, "12")

    def test_delivery_relative_news_controls_reading_order(self):
        already_observed = ObservationChange(is_new=False, price_changed=False)
        price_changed = replace(
            _candidate("changed", bid="12"), change=already_observed
        )
        not_yet_delivered = replace(
            _candidate("unseen", bid="12"), change=already_observed
        )

        plan = plan_candidates(
            [price_changed, not_yet_delivered],
            {("example", "changed"): "10"},
        )

        self.assertEqual(_ids(plan.candidates), ["unseen", "changed"])
        self.assertEqual(plan.candidates[0].score, plan.candidates[1].score)

    def test_a_relisting_is_new_even_when_the_inventory_id_is_the_same(self):
        relisting = _candidate("auction-2", inventory_id="physical-1")

        plan = plan_candidates(
            [relisting], {("example", "auction-1"): "10"}
        )

        self.assertEqual(plan.receipts[0].key, ("example", "auction-2"))
        self.assertTrue(plan.candidates[0].change.is_new)

    def test_repeat_includes_an_unchanged_match_and_calls_it_seen(self):
        candidate = _candidate("same", bid="10")

        plan = plan_candidates(
            [candidate], {("example", "same"): "10"}, repeat=True
        )

        change = plan.candidates[0].change
        self.assertFalse(change.is_new)
        self.assertFalse(change.price_changed)
        self.assertEqual(change.previous_bid, Decimal("10"))
        self.assertEqual(plan.unchanged_matches, 0)

    def test_the_cap_counts_matches_but_receipts_count_auction_events(self):
        first_rule = _candidate("shared", score=90, rule_id="first")
        second_rule = _candidate("shared", score=80, rule_id="second")
        held_back = _candidate("other", score=70)

        plan = plan_candidates(
            [held_back, second_rule, first_rule],
            {},
            limit=2,
        )

        self.assertEqual([item.rule_id for item in plan.candidates], ["first", "second"])
        self.assertEqual(plan.receipts, (DeliveryItem("example", "shared", "10"),))
        self.assertEqual(plan.held_back_matches, 1)

    def test_one_crowded_want_cannot_spend_the_whole_delivery(self):
        """A delivery caps each want first, exactly as the printed report does.

        Without it the highest-scoring want takes every slot: one real run put
        21 car seats into a 75-item email and left five other wants out of it.
        """
        crowded = [
            _candidate(f"seat{index}", score=90, rule_id="car seat")
            for index in range(5)
        ]
        lonely = _candidate("scope", score=80, rule_id="science")

        plan = plan_candidates([*crowded, lonely], {}, limit=3, most_each=2)

        sections = [item.rule_name for item in plan.candidates]
        self.assertEqual(sections.count("Car Seat"), 2)
        self.assertIn("Science", sections)

    def test_a_want_already_delivered_still_offers_its_next_best_few(self):
        """The per-want cap counts what is being sent, not what was ever found.

        Capping before the delivery filter would spend a want's allowance on
        lots this recipient already has, so an evening message would arrive
        short even though the want had more to show.
        """
        already_sent = _candidate("seat1", score=90, rule_id="car seat")
        still_unsent = _candidate("seat2", score=80, rule_id="car seat")

        plan = plan_candidates(
            [already_sent, still_unsent],
            {("example", "seat1"): "10"},
            most_each=1,
        )

        self.assertEqual(_ids(plan.candidates), ["seat2"])

    def test_without_a_per_want_cap_the_ranking_alone_decides(self):
        """The cap is opt-in, so callers that never asked for one are unchanged."""
        crowded = [
            _candidate(f"seat{index}", score=90, rule_id="car seat")
            for index in range(3)
        ]

        plan = plan_candidates(crowded, {}, limit=3)

        self.assertEqual(len(plan.candidates), 3)

    def test_presentation_order_cannot_change_which_match_survives_the_cap(self):
        priority = _candidate("priority", score=90, retail="20")
        expensive = _candidate("expensive", score=40, retail="900")

        plan = plan_candidates([expensive, priority], {}, limit=1)

        self.assertEqual(_ids(plan.candidates), ["priority"])

    def test_candidate_items_project_unique_events_before_planning(self):
        candidates = (
            _candidate("shared", rule_id="first"),
            _candidate("shared", rule_id="second"),
        )

        self.assertEqual(
            candidate_items(candidates),
            (DeliveryItem("example", "shared", "10"),),
        )

    def test_a_nonpositive_cap_is_refused(self):
        with self.assertRaisesRegex(ValueError, "limit must be at least 1"):
            plan_candidates([], {}, limit=0)


class WatchlistPlanTests(unittest.TestCase):
    def test_it_keeps_new_and_changed_items_and_counts_unchanged_ones(self):
        new = _watched("new", "5")
        changed = _watched("changed", "12")
        same = _watched("same", "8")

        plan = plan_watchlist(
            [new, changed, same],
            {
                ("example", "changed"): "10.00",
                ("example", "same"): "8.0",
            },
        )

        self.assertEqual([item.listing_id for item in plan.items], ["new", "changed"])
        self.assertEqual(
            plan.receipts,
            (
                DeliveryItem("example", "new", "5"),
                DeliveryItem("example", "changed", "12"),
            ),
        )
        self.assertEqual(plan.unchanged_items, 1)

    def test_no_price_has_one_stable_unknown_revision(self):
        item = WatchedItem(source="example", listing_id="unseen-price")

        first = plan_watchlist([item], {})
        second = plan_watchlist(
            [item], {("example", "unseen-price"): first.receipts[0].revision}
        )

        self.assertEqual(first.receipts[0].revision, "unknown")
        self.assertEqual(second.items, ())
        self.assertEqual(second.unchanged_items, 1)

    def test_repeat_includes_an_unchanged_watchlist_item(self):
        item = _watched("same", "8")

        plan = plan_watchlist(
            [item], {("example", "same"): "8"}, repeat=True
        )

        self.assertEqual(plan.items, (item,))
        self.assertEqual(plan.unchanged_items, 0)

    def test_watchlist_items_project_revisions_before_planning(self):
        priced = _watched("priced", "8.00")
        unknown = WatchedItem(source="example", listing_id="unknown")

        self.assertEqual(
            watchlist_items((priced, unknown)),
            (
                DeliveryItem("example", "priced", "8"),
                DeliveryItem("example", "unknown", "unknown"),
            ),
        )


class OutcomeFingerprintTests(unittest.TestCase):
    def test_order_and_unlimited_interests_do_not_change_the_fingerprint(self):
        first = _progress("first", "First want", wanted=1, fulfilled=0)
        second = _progress("second", "Second want", wanted=2, fulfilled=1)
        ongoing = _progress("ongoing", "Ongoing", wanted=None, fulfilled=9)

        forward = outcome_fingerprint((first, ongoing, second), 0)
        reverse = outcome_fingerprint((second, first), 0)

        self.assertEqual(forward, reverse)

    def test_each_canonical_outcome_fact_changes_the_fingerprint(self):
        original = _progress("shed", "Metal shed", wanted=1, fulfilled=0)
        fingerprint = outcome_fingerprint((original,), 0)

        changes = (
            replace(original, interest=InterestRef("yard-shed", "Metal shed")),
            replace(original, interest=InterestRef("shed", "Garden shed")),
            replace(original, wanted=2),
            replace(original, fulfilled=1),
        )
        for changed in changes:
            with self.subTest(changed=changed):
                self.assertNotEqual(
                    outcome_fingerprint((changed,), 0), fingerprint
                )
        self.assertNotEqual(outcome_fingerprint((original,), 1), fingerprint)

    def test_an_invalid_unreviewed_count_is_refused(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            outcome_fingerprint((), -1)


class DeliveryRecordTests(unittest.TestCase):
    def test_routes_accept_public_enum_words_and_an_opaque_identity(self):
        fingerprint = "a" * 64
        route = DeliveryRoute("findings", "email", fingerprint)

        self.assertIs(route.report_kind, ReportKind.FINDINGS)
        self.assertIs(route.channel, DeliveryChannel.EMAIL)
        self.assertEqual(route.key, ("findings", "email", fingerprint))

    def test_a_route_or_receipt_cannot_have_an_empty_identity(self):
        with self.assertRaisesRegex(ValueError, "destination_fingerprint"):
            DeliveryRoute(ReportKind.WATCHLIST, DeliveryChannel.WEBHOOK, " ")
        with self.assertRaisesRegex(ValueError, "full lowercase SHA-256"):
            DeliveryRoute(ReportKind.WATCHLIST, DeliveryChannel.WEBHOOK, "A" * 64)
        with self.assertRaisesRegex(ValueError, "full lowercase SHA-256"):
            DeliveryRoute(
                ReportKind.WATCHLIST,
                DeliveryChannel.WEBHOOK,
                f" {'a' * 64} ",
            )
        with self.assertRaisesRegex(ValueError, "listing_id"):
            DeliveryItem("example", "", "10")


def _candidate(
    listing_id: str,
    *,
    bid: str = "10",
    score: int = 50,
    rule_id: str = "wanted",
    inventory_id: str = "",
    retail: str | None = None,
) -> Candidate:
    listing = Listing(
        source="example",
        listing_id=listing_id,
        inventory_id=inventory_id,
        title=f"Example {listing_id}",
        url=f"https://example.invalid/{listing_id}",
        current_bid=Decimal(bid),
        estimated_retail=None if retail is None else Decimal(retail),
    )
    return Candidate(
        listing=listing,
        category=CandidateCategory.WANTED,
        rule_id=rule_id,
        rule_name=rule_id.title(),
        score=score,
        total_cost=listing.current_bid,
        retail_ratio=None,
        reasons=("synthetic match",),
        change=ObservationChange(is_new=True, price_changed=False),
    )


def _watched(listing_id: str, bid: str) -> WatchedItem:
    return WatchedItem(
        source="example",
        listing_id=listing_id,
        readings=(
            PriceReading(
                scanned_at=datetime(2030, 1, 1, tzinfo=UTC),
                current_bid=Decimal(bid),
                total_cost=Decimal(bid),
                listing_id=listing_id,
            ),
        ),
    )


def _progress(
    interest_id: str,
    name: str,
    *,
    wanted: int | None,
    fulfilled: int,
) -> InterestProgress:
    return InterestProgress(
        interest=InterestRef(interest_id, name),
        wanted=wanted,
        fulfilled=fulfilled,
    )


def _ids(candidates: tuple[Candidate, ...]) -> list[str]:
    return [item.listing.listing_id for item in candidates]


if __name__ == "__main__":
    unittest.main()
