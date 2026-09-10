"""Cost estimation, interest matching, and anomaly discovery."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from auction_lens.config import (
    ConditionPolicy,
    InterestDefaults,
    InterestRule,
    LocationPolicy,
)
from auction_lens.models import LogisticsDecision, ObservationChange
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
            self.config,
            scoring=replace(self.config.scoring, rejected_conditions=frozenset({"scrap"})),
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
        matches = evaluate(listing, config)
        self.assertEqual([item for item in matches if item.category == "wanted"], [])

    def test_listing_outside_an_allowed_location_is_skipped(self):
        config = replace(self.config, locations=LocationPolicy(allowed=("north warehouse",)))
        self.assertEqual(evaluate(self.listings[SOUNDBAR], config), [])

    def test_an_ordinary_lot_at_a_far_branch_is_not_worth_the_drive(self):
        config = replace(
            self.config,
            locations=LocationPolicy(far=("example warehouse",), far_minimum_score=99),
        )
        self.assertEqual(evaluate(self.listings[SOUNDBAR], config), [])

    def test_a_standout_at_a_far_branch_still_gets_through(self):
        config = replace(
            self.config,
            locations=LocationPolicy(far=("example warehouse",), far_minimum_score=1),
        )
        self.assertTrue(evaluate(self.listings[SOUNDBAR], config))

    def test_a_near_branch_is_never_held_to_the_far_bar(self):
        config = replace(
            self.config,
            locations=LocationPolicy(far=("somewhere else",), far_minimum_score=100),
        )
        self.assertTrue(evaluate(self.listings[SOUNDBAR], config))

    def test_a_branch_already_being_visited_stops_costing_a_drive(self):
        # The drive is only a cost when it would not otherwise happen.
        listing = self.listings[SOUNDBAR]
        far = LocationPolicy(far=("example warehouse",), far_minimum_score=100)
        today = far.already_visiting(("example warehouse",))
        self.assertEqual(evaluate(listing, replace(self.config, locations=far)), [])
        self.assertTrue(evaluate(listing, replace(self.config, locations=today)))

    def test_visiting_one_branch_says_nothing_about_the_others(self):
        far = LocationPolicy(far=("example warehouse", "far depot"), far_minimum_score=100)
        today = far.already_visiting(("far depot",))
        self.assertEqual(today.far, ("example warehouse",))

    def test_a_trip_named_more_fully_than_the_branch_still_counts(self):
        # Branches are configured as bare names but spoken as whole places.
        far = LocationPolicy(far=("phoenix",), far_minimum_score=100)
        self.assertEqual(far.already_visiting(("Phoenix, AZ",)).far, ())

    def test_naming_no_trips_leaves_the_map_exactly_as_it_was(self):
        far = LocationPolicy(far=("example warehouse",), far_minimum_score=100)
        self.assertIs(far.already_visiting(()), far)

    def test_ending_soon_adds_a_reason_and_raises_the_score(self):
        now = datetime(2026, 9, 5, 12, tzinfo=UTC)
        listing = replace(self.listings[SOUNDBAR], ends_at=now + timedelta(minutes=5))
        soon = self._wanted(evaluate(listing, self.config, now=now))
        later = self._wanted(evaluate(self.listings[SOUNDBAR], self.config, now=now))
        self.assertIn("ending soon", soon.reasons)
        self.assertGreater(soon.score, later.score)

    def test_closed_listing_gets_no_urgency_bonus(self):
        now = datetime(2026, 9, 5, 12, tzinfo=UTC)
        listing = replace(self.listings[SOUNDBAR], ends_at=now - timedelta(minutes=1))
        candidate = self._wanted(evaluate(listing, self.config, now=now))
        self.assertNotIn("ending soon", candidate.reasons)

    def test_fresh_news_reorders_a_match_without_changing_its_quality_score(self):
        listing = self.listings[SOUNDBAR]
        new = self._wanted(
            evaluate(listing, self.config, ObservationChange(True, False))
        )
        unchanged = self._wanted(
            evaluate(listing, self.config, ObservationChange(False, False))
        )

        self.assertEqual(new.score, unchanged.score)
        self.assertGreater(new.priority, unchanged.priority)

    def test_freshness_alone_cannot_push_a_match_past_its_rule_bar(self):
        listing = self.listings[SOUNDBAR]
        ordinary = self._wanted(
            evaluate(listing, self.config, ObservationChange(False, False))
        )
        rule = replace(self.config.interests[0], minimum_score=ordinary.score + 1)
        config = replace(self.config, interests=(rule,))

        matches = evaluate(listing, config, ObservationChange(True, False))

        self.assertFalse(any(item.category == "wanted" for item in matches))

    def test_freshness_alone_cannot_justify_a_far_trip(self):
        listing = self.listings[SOUNDBAR]
        ordinary = self._wanted(
            evaluate(listing, self.config, ObservationChange(False, False))
        )
        config = replace(
            self.config,
            locations=LocationPolicy(
                far=("example warehouse",),
                far_minimum_score=ordinary.score + 1,
            ),
        )

        matches = evaluate(listing, config, ObservationChange(True, False))

        self.assertFalse(any(item.category == "wanted" for item in matches))

    def _wanted(self, candidates):
        return next(item for item in candidates if item.category == "wanted")


class MinimumRetailTests(unittest.TestCase):
    """The floor that separates a thing from its accessories."""

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _rule(self, **overrides):
        settings = {"name": "guitar", "any_terms": ("guitar",), "minimum_retail": Decimal("60")}
        settings.update(overrides)
        return replace(self.config, interests=(InterestRule(**settings),))

    def _matches(self, config, **listing_overrides):
        listing = replace(self.listings[SOUNDBAR], **listing_overrides)
        scored = evaluate(listing, config)
        return [item.rule_name for item in scored if item.category == "wanted"]

    def test_an_accessory_worth_less_than_the_floor_is_not_the_thing(self):
        # A guitar cable says "guitar" as loudly as a guitar does.
        self.assertEqual(
            self._matches(
                self._rule(), title="Guitar Cable 10ft", estimated_retail=Decimal("15")
            ),
            [],
        )

    def test_the_thing_itself_still_matches(self):
        self.assertEqual(
            self._matches(
                self._rule(), title="Fender Guitar", estimated_retail=Decimal("300")
            ),
            ["guitar"],
        )

    def test_a_floor_nobody_can_check_is_not_cleared(self):
        self.assertEqual(
            self._matches(self._rule(), title="Some Guitar", estimated_retail=None), []
        )

    def test_a_rule_naming_no_floor_still_takes_anything(self):
        self.assertEqual(
            self._matches(
                self._rule(minimum_retail=None),
                title="Guitar Cable 10ft",
                estimated_retail=Decimal("15"),
            ),
            ["guitar"],
        )

    def test_a_negative_floor_is_refused(self):
        with self.assertRaisesRegex(ValueError, "minimum_retail"):
            InterestRule(name="x", minimum_retail=Decimal("-1"))


class WarehouseNoteTests(unittest.TestCase):
    """The one sentence written about this item rather than about the model.

    A title is the manufacturer's words and is the same on every copy of a
    product. The note is what somebody wrote after looking at this actual lot,
    so it is the only place a missing blower or a leaking seam is ever said.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _matches(self, *, notes="", title="Inflatable Water Slide Bounce House"):
        rule = InterestRule(
            name="bounce house",
            any_terms=("water slide",),
            exclude_terms=("blower not included",),
        )
        listing = replace(
            self.listings[SOUNDBAR],
            title=title,
            notes=notes,
            estimated_retail=Decimal("300"),
        )
        config = replace(self.config, interests=(rule,))
        scored = evaluate(listing, config)
        return [item.rule_name for item in scored if item.category == "wanted"]

    def test_a_note_can_rule_a_lot_out(self):
        # The seller said the fan is missing, which no title would ever say.
        self.assertEqual(self._matches(notes="9/8 blower not included"), [])

    def test_saying_nothing_is_not_the_same_as_saying_no(self):
        # Almost nobody names the blower, so silence has to stay a pass.
        self.assertEqual(self._matches(notes="updated 9/8"), ["bounce house"])

    def test_a_note_written_across_several_lines_is_still_read(self):
        # People type these into a box over several visits, and where they
        # pressed Enter must not decide whether the lot is reported.
        self.assertEqual(
            self._matches(notes="9/8 blower\nnot included\nleaks air"), []
        )

    def test_a_note_cannot_make_a_lot_match(self):
        # A pallet lists its contents in the note. Reading wants from there
        # would make one pallet answer every interest at once.
        self.assertEqual(
            self._matches(
                title="Nellis XL Pallet: Mixed Returns",
                notes="7 items including an inflatable water slide",
            ),
            [],
        )


class AccessoryTests(unittest.TestCase):
    """The other half of telling a thing from what attaches to it.

    The value floor above catches the cheap accessories. These catch the ones
    that cost real money: a set of guitar hangers outsells a beginner guitar.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _matches(self, title, *, rule=None):
        rule = rule or InterestRule(
            name="guitar",
            any_terms=("guitar",),
            accessory_nouns=("stand", "hanger", "case"),
        )
        config = replace(self.config, interests=(rule,))
        listing = replace(
            self.listings[SOUNDBAR], title=title, estimated_retail=Decimal("300")
        )
        scored = evaluate(listing, config)
        return [item.rule_name for item in scored if item.category == "wanted"]

    def test_a_fitting_named_beside_the_thing_is_not_the_thing(self):
        self.assertEqual(self._matches("Hercules Guitar Hangers, Set of 3"), [])

    def test_the_same_word_further_away_still_leaves_the_thing(self):
        # A guitar sold with a stand is a guitar. Only nearness means accessory.
        self.assertEqual(
            self._matches("Fender Guitar Bundle with Amp, Strap and Stand"), ["guitar"]
        )

    def test_a_word_between_them_is_still_beside_the_thing(self):
        self.assertEqual(self._matches("CAHAYA Acoustic Guitar Hard Case"), [])

    def test_a_thing_shaped_like_the_thing_is_not_the_thing(self):
        # A plastic guitar that plugs into a games console says "guitar" first,
        # so the "for" rule cannot see it: only the noun beside it can.
        rule = InterestRule(
            name="guitar", any_terms=("guitar",), accessory_nouns=("controller",)
        )
        self.assertEqual(
            self._matches("Lyvix Wireless Guitar Controller for PS4/PS3/PC", rule=rule),
            [],
        )

    def test_the_same_noun_serves_a_rule_that_wants_something_else(self):
        # One shared word, two rules: a bike controller is a spare motor part
        # exactly as a guitar controller is a toy. Neither rule had to know.
        rule = InterestRule(
            name="e-bike", any_terms=("electric bike",), accessory_nouns=("controller",)
        )
        self.assertEqual(
            self._matches("Aramox 52V 1200W Electric Bike Controller Kit", rule=rule), []
        )

    def test_a_longer_word_that_merely_begins_the_same_is_not_it(self):
        rule = InterestRule(
            name="e-bike", any_terms=("electric bike",), accessory_nouns=("mount",)
        )
        self.assertEqual(
            self._matches("Electric Bike, Mountain Trail Model", rule=rule), ["e-bike"]
        )

    def test_naming_the_thing_only_after_for_makes_it_an_accessory(self):
        rule = InterestRule(name="tools", any_terms=("dewalt",))
        self.assertEqual(
            self._matches("Cordless Weed Wacker for DeWalt 20V Battery", rule=rule), []
        )

    def test_saying_who_the_thing_is_for_does_not_make_it_one(self):
        # The wanted words come first, so this is an electric bike that happens
        # to say who it suits -- not a fitting that attaches to one.
        rule = InterestRule(name="e-bike", any_terms=("electric bike",))
        self.assertEqual(
            self._matches("Caroma Electric Bike for Adults, 48V Battery", rule=rule),
            ["e-bike"],
        )

    def test_a_rule_naming_no_wanted_words_is_left_alone(self):
        # Nothing to be positioned relative to "for", so the question does not
        # arise. Asking it anyway would reject every title containing "for".
        rule = InterestRule(name="tubing", all_terms=("square", "tubing"))
        self.assertEqual(
            self._matches("Square Steel Tubing for Sale, 3 Lengths", rule=rule),
            ["tubing"],
        )

    def test_a_rule_that_asks_for_the_word_keeps_it(self):
        defaults = InterestDefaults(
            exclude_terms=("replacement",), accessory_nouns=("stand",)
        )
        rule = defaults.applied_to(
            InterestRule(name="guitar stand", any_terms=("guitar stand",))
        )
        self.assertEqual(rule.accessory_nouns, ())
        self.assertEqual(self._matches("DIDA Guitar Stand", rule=rule), ["guitar stand"])

    def test_a_rule_inherits_words_it_never_named(self):
        rule = InterestDefaults(exclude_terms=("compatible with",)).applied_to(
            InterestRule(name="tools", any_terms=("dewalt",))
        )
        self.assertIn("compatible with", rule.exclude_terms)
        self.assertEqual(self._matches("Heat Gun Compatible With Dewalt", rule=rule), [])

    def test_a_word_the_rule_already_named_is_not_inherited_twice(self):
        rule = InterestDefaults(exclude_terms=("adapter",)).applied_to(
            InterestRule(name="tools", any_terms=("dewalt",), exclude_terms=("adapter",))
        )
        self.assertEqual(rule.exclude_terms, ("adapter",))


class InterestWeightTests(unittest.TestCase):
    """Weight decides what is read first, never what is allowed through."""

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_a_wanted_lot_outranks_a_bargain_nobody_asked_for(self):
        scored = evaluate(self.listings[SOUNDBAR], self.config)
        candidates = {item.category: item for item in scored}
        wanted, anomaly = candidates["wanted"], candidates["anomaly"]
        # The anomaly scores higher on raw discount; wanting the thing decides.
        self.assertGreater(anomaly.score, wanted.score)
        self.assertGreater(wanted.priority, anomaly.priority)

    def test_a_sunken_weight_still_reports_the_lot(self):
        # The catch-all exists to surface things no rule mentions, so weighting
        # it down must reorder it, not silence it.
        config = replace(
            self.config, scoring=replace(self.config.scoring, anomaly_weight=Decimal("0"))
        )
        candidates = evaluate(self.listings[LASER_LEVEL], config)
        anomaly = next(item for item in candidates if item.category == "anomaly")
        self.assertEqual(anomaly.priority, 0)
        self.assertGreaterEqual(anomaly.score, config.scoring.minimum_report_score)

    def test_raising_a_weight_cannot_push_a_lot_past_a_bar_it_failed(self):
        eager = InterestRule(name="soundbar", any_terms=("sound bar",), minimum_score=99,
                             weight=Decimal("10"))
        config = replace(self.config, interests=(eager,))
        scored = evaluate(self.listings[SOUNDBAR], config)
        self.assertEqual([item for item in scored if item.category == "wanted"], [])

    def test_a_negative_weight_is_refused(self):
        with self.assertRaisesRegex(ValueError, "weight"):
            InterestRule(name="anything", weight=Decimal("-1"))


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

    def test_freshness_alone_cannot_push_an_anomaly_past_the_global_bar(self):
        listing = self.listings[LASER_LEVEL]
        ordinary = self._anomaly(
            evaluate(listing, self.config, ObservationChange(False, False))
        )
        config = replace(
            self.config,
            scoring=replace(
                self.config.scoring,
                minimum_report_score=ordinary.score + 1,
            ),
        )

        self.assertEqual(
            evaluate(listing, config, ObservationChange(True, False)), []
        )

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
