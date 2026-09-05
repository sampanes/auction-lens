"""Cost estimation, interest matching, and anomaly discovery."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from auction_lens.config import ConditionPolicy, InterestRule
from auction_lens.models import LogisticsDecision
from auction_lens.scoring import estimate_total_cost, evaluate
from support import LASER_LEVEL, SOUNDBAR, example_config, example_listings


class TotalCostTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_listing_premium_overrides_the_configured_default(self):
        listing = self.listings[SOUNDBAR]
        self.assertEqual(estimate_total_cost(listing, self.config.economics), Decimal("20.70"))

    def test_processing_fee_and_tax_are_added_to_the_premium(self):
        economics = replace(
            self.config.economics,
            sales_tax_rate=Decimal("0.10"),
            processing_fee=Decimal("3.00"),
        )
        listing = replace(self.listings[SOUNDBAR], buyer_premium_rate=Decimal("0"))
        # 18.00 bid + 0 premium + 1.80 tax + 3.00 fee
        self.assertEqual(estimate_total_cost(listing, economics), Decimal("22.80"))


class InterestScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_one_listing_can_match_an_interest_and_an_anomaly(self):
        candidates = evaluate(self.listings[SOUNDBAR], self.config)
        self.assertEqual({item.category for item in candidates}, {"wanted", "anomaly"})

    def test_globally_rejected_condition_never_becomes_a_candidate(self):
        config = replace(
            self.config, scoring=replace(self.config.scoring, rejected_conditions=frozenset({"scrap"}))
        )
        scrap = replace(self.listings[SOUNDBAR], conditions=("scrap",))
        self.assertEqual(evaluate(scrap, config), [])

    def test_condition_rejected_by_a_profile_never_becomes_a_candidate(self):
        broken = replace(self.listings[LASER_LEVEL], conditions=("not functional",))
        self.assertEqual(evaluate(broken, self.config), [])

    def test_condition_policy_is_scoped_to_the_intended_use(self):
        salvage = InterestRule(
            name="square tubing stock",
            purpose="salvage",
            all_terms=("square", "tubing"),
            condition=ConditionPolicy(reject=frozenset()),
        )
        config = replace(self.config, interests=(salvage,))
        listing = replace(
            self.listings[SOUNDBAR],
            title="Broken work stand with square steel tubing",
            estimated_retail=None,
            conditions=("not functional", "1 of 3 working"),
        )
        candidates = evaluate(listing, config)
        self.assertEqual([item.rule_name for item in candidates], ["square tubing stock"])
        self.assertIn("salvage interest", candidates[0].reasons[0])

    def test_excluded_term_disqualifies_an_otherwise_matching_listing(self):
        rule = InterestRule(name="soundbar", any_terms=("sound bar",), exclude_terms=("mount",))
        config = replace(self.config, interests=(rule,))
        listing = replace(self.listings[SOUNDBAR], title="Sound bar wall mount bracket")
        self.assertEqual([item for item in evaluate(listing, config) if item.category == "wanted"], [])

    def test_listing_outside_an_allowed_location_is_skipped(self):
        config = replace(self.config, allowed_locations=("north warehouse",))
        self.assertEqual(evaluate(self.listings[SOUNDBAR], config), [])

    def test_ending_soon_adds_a_reason_and_raises_the_score(self):
        now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        listing = replace(self.listings[SOUNDBAR], ends_at=now + timedelta(minutes=5))
        soon = self._wanted(evaluate(listing, self.config, now=now))
        later = self._wanted(evaluate(self.listings[SOUNDBAR], self.config, now=now))
        self.assertIn("ending soon", soon.reasons)
        self.assertGreater(soon.score, later.score)

    def test_closed_listing_gets_no_urgency_bonus(self):
        now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        listing = replace(self.listings[SOUNDBAR], ends_at=now - timedelta(minutes=1))
        candidate = self._wanted(evaluate(listing, self.config, now=now))
        self.assertNotIn("ending soon", candidate.reasons)

    def _wanted(self, candidates):
        return next(item for item in candidates if item.category == "wanted")


class AnomalyScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_deep_discount_on_a_valuable_item_is_reported(self):
        candidate = self._anomaly(evaluate(self.listings[LASER_LEVEL], self.config))
        self.assertEqual(candidate.rule_name, "retail-ratio")
        self.assertIn("of stated retail", candidate.reasons[0])

    def test_retail_below_the_floor_is_not_an_anomaly(self):
        listing = replace(self.listings[LASER_LEVEL], estimated_retail=Decimal("60.00"))
        self.assertEqual(evaluate(listing, self.config), [])

    def test_listing_without_retail_is_not_an_anomaly(self):
        listing = replace(self.listings[LASER_LEVEL], estimated_retail=None)
        self.assertEqual(evaluate(listing, self.config), [])

    def _anomaly(self, candidates):
        return next(item for item in candidates if item.category == "anomaly")


class LogisticsScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_saved_decision_adds_its_cost_and_can_price_a_listing_out(self):
        listing = replace(self.listings[SOUNDBAR], handling_weight_lb=Decimal("148"))
        feasible = LogisticsDecision(status="feasible", added_cost=Decimal("10"))
        candidate = next(
            item
            for item in evaluate(listing, self.config, logistics_decision=feasible)
            if item.category == "wanted"
        )
        self.assertEqual(candidate.total_cost, Decimal("30.70"))
        self.assertEqual(candidate.logistics.status, "feasible")

        too_expensive = replace(feasible, added_cost=Decimal("40"))
        self.assertEqual(evaluate(listing, self.config, logistics_decision=too_expensive), [])

    def test_infeasible_decision_suppresses_the_listing_entirely(self):
        listing = replace(self.listings[SOUNDBAR], handling_weight_lb=Decimal("148"))
        decision = LogisticsDecision(status="infeasible")
        self.assertEqual(evaluate(listing, self.config, logistics_decision=decision), [])


if __name__ == "__main__":
    unittest.main()
