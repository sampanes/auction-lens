"""Finite interests retire only from explicit, reversible purchase outcomes."""

from __future__ import annotations

import unittest
from dataclasses import replace

from auction_lens.models import InterestRef, Verdict, WatchedItem
from auction_lens.outcomes import plan_interests
from support import example_config


class InterestPlanTests(unittest.TestCase):
    def setUp(self):
        rule = replace(
            example_config().interests[0],
            interest_id="audio",
            name="living-room soundbar",
            wanted=1,
        )
        self.rules = (rule,)
        self.reference = InterestRef("audio", "living-room soundbar")

    def test_an_unlimited_interest_never_retires(self):
        unlimited = replace(self.rules[0], wanted=None)
        won = self._item(fulfilled=(self.reference,))

        plan = plan_interests((unlimited,), (won,))

        self.assertEqual(plan.active_rules, (unlimited,))
        self.assertFalse(plan.progress[0].is_retired)

    def test_a_target_retires_at_the_confirmed_quantity(self):
        plan = plan_interests(self.rules, (self._item(fulfilled=(self.reference,)),))

        self.assertEqual(plan.active_rules, ())
        self.assertTrue(plan.progress[0].is_retired)
        self.assertEqual(plan.progress[0].remaining, 0)

    def test_winning_a_multi_match_lot_fulfills_only_the_named_interest(self):
        other = replace(
            self.rules[0], interest_id="workshop", name="workshop audio"
        )
        other_ref = InterestRef("workshop", "workshop audio")
        item = self._item(
            matched=(self.reference, other_ref), fulfilled=(self.reference,)
        )

        plan = plan_interests((*self.rules, other), (item,))

        self.assertEqual(
            [status.fulfilled for status in plan.progress],
            [1, 0],
        )
        self.assertEqual(plan.active_rules, (other,))

    def test_a_legacy_win_without_provenance_is_loaded_but_never_guessed(self):
        item = self._item(matched=(), fulfilled=())

        plan = plan_interests(self.rules, (item,))

        self.assertEqual(plan.active_rules, self.rules)
        self.assertEqual(plan.progress[0].fulfilled, 0)
        self.assertEqual(plan.unreviewed_wins, 0)

    def test_a_finite_match_won_without_an_allocation_is_called_out(self):
        plan = plan_interests(self.rules, (self._item(fulfilled=()),))

        self.assertEqual(plan.active_rules, self.rules)
        self.assertEqual(plan.unreviewed_wins, 1)

    def test_a_reviewed_win_that_fulfilled_none_does_not_keep_asking(self):
        item = replace(
            self._item(fulfilled=()),
            fulfillment_reviewed=True,
        )

        plan = plan_interests(self.rules, (item,))

        self.assertEqual(plan.active_rules, self.rules)
        self.assertEqual(plan.progress[0].fulfilled, 0)
        self.assertEqual(plan.unreviewed_wins, 0)

    def test_an_ongoing_match_does_not_ask_for_an_irrelevant_allocation(self):
        ongoing = replace(
            self.rules[0], interest_id="ongoing", name="ongoing interest", wanted=None
        )
        reference = InterestRef("ongoing", "ongoing interest")
        item = self._item(matched=(reference,), fulfilled=())

        plan = plan_interests((*self.rules, ongoing), (item,))

        self.assertEqual(plan.unreviewed_wins, 0)

    def test_changing_the_verdict_reactivates_without_erasing_history(self):
        item = self._item(fulfilled=(self.reference,), verdict=Verdict.LOST)

        plan = plan_interests(self.rules, (item,))

        self.assertEqual(plan.active_rules, self.rules)
        self.assertEqual(plan.progress[0].fulfilled, 0)

    def test_raising_the_target_reactivates_the_interest(self):
        needs_two = replace(self.rules[0], wanted=2)

        plan = plan_interests(
            (needs_two,), (self._item(fulfilled=(self.reference,)),)
        )

        self.assertEqual(plan.active_rules, (needs_two,))
        self.assertEqual(plan.progress[0].remaining, 1)

    def test_a_duplicated_watchlist_row_does_not_count_the_same_lot_twice(self):
        needs_two = replace(self.rules[0], wanted=2)
        won = self._item(fulfilled=(self.reference,))

        plan = plan_interests((needs_two,), (won, won))

        self.assertEqual(plan.progress[0].fulfilled, 1)
        self.assertEqual(plan.progress[0].remaining, 1)

    def test_duplicate_rows_merge_distinct_allocations_regardless_of_order(self):
        other = replace(
            self.rules[0], interest_id="workshop", name="workshop audio"
        )
        other_ref = InterestRef("workshop", "workshop audio")
        audio = self._item(
            matched=(self.reference, other_ref), fulfilled=(self.reference,)
        )
        workshop = self._item(
            matched=(self.reference, other_ref), fulfilled=(other_ref,)
        )

        forward = plan_interests((*self.rules, other), (audio, workshop))
        reverse = plan_interests((*self.rules, other), (workshop, audio))

        self.assertEqual([status.fulfilled for status in forward.progress], [1, 1])
        self.assertEqual(reverse, forward)

    def test_duplicate_rows_merge_a_reviewed_none_answer_regardless_of_order(self):
        unreviewed = self._item(fulfilled=())
        reviewed = replace(unreviewed, fulfillment_reviewed=True)

        forward = plan_interests(self.rules, (unreviewed, reviewed))
        reverse = plan_interests(self.rules, (reviewed, unreviewed))

        self.assertEqual(forward.unreviewed_wins, 0)
        self.assertEqual(reverse, forward)

    def _item(
        self,
        *,
        matched: tuple[InterestRef, ...] | None = None,
        fulfilled: tuple[InterestRef, ...],
        verdict: Verdict = Verdict.WON,
    ) -> WatchedItem:
        return WatchedItem(
            source="example",
            listing_id="lot-1",
            matched_interests=(self.reference,) if matched is None else matched,
            fulfilled_interests=fulfilled,
            verdict=verdict,
        )


if __name__ == "__main__":
    unittest.main()
