"""Which handling stages a listing leaves unresolved."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from auction_lens.logistics import assess_logistics
from auction_lens.models import LogisticsDecision
from support import SOUNDBAR, example_config, example_listings

HEAVY_AND_LARGE = {
    "handling_weight_lb": Decimal("148"),
    "package_dimensions_in": (Decimal("70"), Decimal("31"), Decimal("45")),
}


class LogisticsAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config().logistics
        self.listing = example_listings()[SOUNDBAR]

    def test_ordinary_listing_asks_nothing(self):
        assessment = assess_logistics(self.listing, self.config)
        self.assertEqual(assessment.status, "ordinary")
        self.assertEqual(assessment.questions, ())

    def test_loading_assistance_leaves_only_destination_handling_open(self):
        listing = replace(self.listing, loading_assistance=("forklift",), **HEAVY_AND_LARGE)
        questions = " ".join(assess_logistics(listing, self.config).questions).lower()
        self.assertIn("fits the planned transport", questions)
        self.assertIn("destination unloading", questions)
        self.assertNotIn("origin loading", questions)

    def test_question_states_dimensions_the_way_they_were_given(self):
        listing = replace(self.listing, **HEAVY_AND_LARGE)
        questions = " ".join(assess_logistics(listing, self.config).questions)
        self.assertIn("70 x 31 x 45 in item", questions)
        self.assertIn("148 lb", questions)

    def test_unassisted_heavy_item_asks_about_both_ends(self):
        listing = replace(self.listing, **HEAVY_AND_LARGE)
        questions = " ".join(assess_logistics(listing, self.config).questions).lower()
        self.assertIn("origin loading and destination unloading", questions)

    def test_reject_policy_makes_a_large_item_infeasible(self):
        listing = replace(self.listing, **HEAVY_AND_LARGE)
        config = replace(self.config, large_item_policy="reject")
        self.assertEqual(assess_logistics(listing, config).status, "infeasible")

    def test_allow_policy_assumes_a_large_item_is_manageable(self):
        listing = replace(self.listing, **HEAVY_AND_LARGE)
        config = replace(self.config, large_item_policy="allow")
        self.assertEqual(assess_logistics(listing, config).status, "assumed_feasible")

    def test_saved_decision_replaces_every_question(self):
        listing = replace(self.listing, **HEAVY_AND_LARGE)
        decision = LogisticsDecision(status="feasible", added_cost=Decimal("25"), note="van")
        assessment = assess_logistics(listing, self.config, decision)
        self.assertEqual(assessment.status, "feasible")
        self.assertEqual(assessment.questions, ())
        self.assertEqual(assessment.added_cost, Decimal("25"))
        self.assertEqual(assessment.decision_note, "van")

    def test_unknown_decision_status_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown logistics decision"):
            assess_logistics(self.listing, self.config, LogisticsDecision(status="maybe"))


if __name__ == "__main__":
    unittest.main()
