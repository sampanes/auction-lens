"""Cost estimation, interest matching, and anomaly discovery."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from auction_lens.config.interests import (
    ConditionPolicy,
    InterestDefaults,
    InterestRule,
    ScoringConfig,
)
from auction_lens.config.logistics import LocationPolicy
from auction_lens.listings.model import ObservationChange
from auction_lens.matching.evaluate import estimate_total_cost, evaluate
from auction_lens.matching.logistics import LogisticsDecision
from support import (
    LASER_LEVEL,
    SOUNDBAR,
    example_config,
    example_listings,
    shortly_before_close,
)

# When these tests are reading. The shared fixtures end far in the future, so
# both scoring bars would otherwise hold every lot back: the bargain path
# because an opening bid is not a discount, the wanted path because a lot a day
# out has no price to judge. Half an hour before the close is inside both and
# outside the ending-soon bonus, so scores read as they would on the night.
READING = shortly_before_close()


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
        candidates = evaluate(
            self.listings[SOUNDBAR], self.config, now=READING
        )
        self.assertEqual({item.category for item in candidates}, {"wanted", "anomaly"})

    def test_globally_rejected_condition_never_becomes_a_candidate(self):
        config = replace(
            self.config,
            scoring=replace(self.config.scoring, rejected_conditions=frozenset({"scrap"})),
        )
        scrap = replace(self.listings[SOUNDBAR], conditions=("scrap",))
        self.assertEqual(evaluate(scrap, config, now=READING), [])

    def test_condition_rejected_by_a_profile_never_becomes_a_candidate(self):
        broken = replace(self.listings[LASER_LEVEL], conditions=("not functional",))
        self.assertEqual(evaluate(broken, self.config, now=READING), [])

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
        candidates = evaluate(listing, config, now=READING)
        self.assertEqual([item.rule_name for item in candidates], ["square tubing stock"])
        self.assertIn("salvage interest", candidates[0].reasons[0])

    def test_an_accessory_word_beside_the_wanted_word_disqualifies_it(self):
        rule = InterestRule(
            name="soundbar", any_terms=("sound bar",), accessory_nouns=("mount",)
        )
        config = replace(self.config, interests=(rule,))
        listing = replace(self.listings[SOUNDBAR], title="Sound bar wall mount bracket")
        matches = evaluate(listing, config, now=READING)
        self.assertEqual([item for item in matches if item.category == "wanted"], [])

    def test_a_lot_asking_most_of_retail_is_never_reported(self):
        """Wanting a thing must not argue for paying near retail at auction.

        A gate rather than a penalty: a real run reported a laptop at 86% of
        its stated retail as a good match, because a high enough score could
        outrank the price.
        """
        config = replace(
            self.config,
            scoring=replace(self.config.scoring, maximum_retail_ratio=Decimal("0.60")),
        )
        listing = self.listings[SOUNDBAR]
        near_retail = replace(listing, current_bid=listing.estimated_retail)
        self.assertEqual(evaluate(near_retail, config, now=READING), [])

    def test_the_ceiling_leaves_a_fairly_priced_lot_alone(self):
        config = replace(
            self.config,
            scoring=replace(self.config.scoring, maximum_retail_ratio=Decimal("0.60")),
        )
        self.assertNotEqual(evaluate(self.listings[SOUNDBAR], config, now=READING), [])

    def test_a_lot_stating_no_retail_is_not_judged_by_the_ceiling(self):
        """No stated retail states no ratio, so there is nothing to exceed."""
        config = replace(
            self.config,
            scoring=replace(self.config.scoring, maximum_retail_ratio=Decimal("0.60")),
        )
        unpriced = replace(self.listings[SOUNDBAR], estimated_retail=None)
        wanted = [
            item
            for item in evaluate(unpriced, config, now=READING)
            if item.category == "wanted"
        ]
        self.assertNotEqual(wanted, [])

    def test_listing_outside_an_allowed_location_is_skipped(self):
        config = replace(self.config, locations=LocationPolicy(allowed=("north warehouse",)))
        self.assertEqual(evaluate(self.listings[SOUNDBAR], config, now=READING), [])

    def test_an_ordinary_lot_at_a_far_branch_is_not_worth_the_drive(self):
        config = replace(
            self.config,
            locations=LocationPolicy(far=("example warehouse",), far_minimum_score=99),
        )
        self.assertEqual(evaluate(self.listings[SOUNDBAR], config, now=READING), [])

    def test_a_standout_at_a_far_branch_still_gets_through(self):
        config = replace(
            self.config,
            locations=LocationPolicy(far=("example warehouse",), far_minimum_score=1),
        )
        self.assertTrue(evaluate(self.listings[SOUNDBAR], config, now=READING))

    def test_a_near_branch_is_never_held_to_the_far_bar(self):
        config = replace(
            self.config,
            locations=LocationPolicy(far=("somewhere else",), far_minimum_score=100),
        )
        self.assertTrue(evaluate(self.listings[SOUNDBAR], config, now=READING))

    def test_a_small_lot_at_a_far_branch_is_not_worth_the_drive_however_cheap(self):
        """The score cannot say this on its own.

        A price anomaly scores a share of retail turned around, so a nearly
        free lot scores nearly 100 no matter how small the thing is. The bar
        below is set to 1 to prove the point: even with the score bar giving
        the lot everything, a 129 dollar item does not justify the drive.
        """
        config = replace(
            self.config,
            locations=LocationPolicy(
                far=("example warehouse",),
                far_minimum_score=1,
                far_minimum_retail=Decimal("500"),
            ),
        )
        self.assertEqual(evaluate(self.listings[SOUNDBAR], config, now=READING), [])

    def test_a_big_enough_lot_at_a_far_branch_still_comes_through(self):
        config = replace(
            self.config,
            locations=LocationPolicy(
                far=("example warehouse",),
                far_minimum_score=1,
                far_minimum_retail=Decimal("100"),
            ),
        )
        self.assertTrue(evaluate(self.listings[SOUNDBAR], config, now=READING))

    def test_a_far_lot_stating_no_retail_cannot_prove_it_is_worth_the_drive(self):
        """Fails closed. An unstated size is not evidence of a large one."""
        config = replace(
            self.config,
            locations=LocationPolicy(
                far=("example warehouse",),
                far_minimum_score=1,
                far_minimum_retail=Decimal("100"),
            ),
        )
        unpriced = replace(self.listings[SOUNDBAR], estimated_retail=None)
        self.assertEqual(evaluate(unpriced, config, now=READING), [])

    def test_without_a_floor_an_unpriced_far_lot_is_judged_on_score_alone(self):
        """The floor is what closes the door, so no floor leaves it open."""
        config = replace(
            self.config,
            locations=LocationPolicy(far=("example warehouse",), far_minimum_score=1),
        )
        unpriced = replace(self.listings[SOUNDBAR], estimated_retail=None)
        self.assertTrue(evaluate(unpriced, config, now=READING))

    def test_a_near_branch_is_never_held_to_the_retail_floor(self):
        config = replace(
            self.config,
            locations=LocationPolicy(
                far=("somewhere else",), far_minimum_retail=Decimal("5000")
            ),
        )
        self.assertTrue(evaluate(self.listings[SOUNDBAR], config, now=READING))

    def test_a_branch_already_being_visited_stops_costing_a_drive(self):
        # The drive is only a cost when it would not otherwise happen.
        listing = self.listings[SOUNDBAR]
        far = LocationPolicy(far=("example warehouse",), far_minimum_score=100)
        today = far.already_visiting(("example warehouse",))
        far_config = replace(self.config, locations=far)
        self.assertEqual(evaluate(listing, far_config, now=READING), [])
        self.assertTrue(evaluate(listing, replace(self.config, locations=today), now=READING))

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
        # Three hours out, which is reported but is not ending soon, so the
        # bonus under test is the only difference between the two readings.
        # The fixture's own far-future end is past the wanted bar entirely and
        # would compare a scored lot against no lot at all.
        unhurried = replace(self.listings[SOUNDBAR], ends_at=now + timedelta(hours=3))
        soon = self._wanted(evaluate(listing, self.config, now=now))
        later = self._wanted(evaluate(unhurried, self.config, now=now))
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
            evaluate(listing, self.config, ObservationChange(True, False), now=READING)
        )
        unchanged = self._wanted(
            evaluate(listing, self.config, ObservationChange(False, False), now=READING)
        )

        self.assertEqual(new.score, unchanged.score)
        self.assertGreater(new.priority, unchanged.priority)

    def test_freshness_alone_cannot_push_a_match_past_its_rule_bar(self):
        listing = self.listings[SOUNDBAR]
        ordinary = self._wanted(
            evaluate(listing, self.config, ObservationChange(False, False), now=READING)
        )
        rule = replace(self.config.interests[0], minimum_score=ordinary.score + 1)
        config = replace(self.config, interests=(rule,))

        matches = evaluate(listing, config, ObservationChange(True, False), now=READING)

        self.assertFalse(any(item.category == "wanted" for item in matches))

    def test_freshness_alone_cannot_justify_a_far_trip(self):
        listing = self.listings[SOUNDBAR]
        ordinary = self._wanted(
            evaluate(listing, self.config, ObservationChange(False, False), now=READING)
        )
        config = replace(
            self.config,
            locations=LocationPolicy(
                far=("example warehouse",),
                far_minimum_score=ordinary.score + 1,
            ),
        )

        matches = evaluate(listing, config, ObservationChange(True, False), now=READING)

        self.assertFalse(any(item.category == "wanted" for item in matches))

    def _wanted(self, candidates):
        return next(item for item in candidates if item.category == "wanted")


class UnknownConditionTests(unittest.TestCase):
    """A policy strict about the unknown is also strict about the flawless.

    A grade contributes only its answers that are not good news, so the lot
    with nothing wrong with it and the lot nobody graded arrive looking the
    same: no labels either way. These pin which way that reads, because the
    flag's name suggests it reaches only the second one.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _matches(self, conditions, policy):
        rule = InterestRule(
            name="soundbar", any_terms=("sound bar",), condition=policy
        )
        config = replace(self.config, interests=(rule,))
        listing = replace(self.listings[SOUNDBAR], conditions=conditions)
        scored = evaluate(listing, config, now=READING)
        return [item.rule_name for item in scored if item.category == "wanted"]

    def test_a_flawless_lot_is_admitted(self):
        self.assertEqual(self._matches((), ConditionPolicy()), ["soundbar"])

    def test_refusing_the_unknown_refuses_the_flawless_lot_too(self):
        self.assertEqual(self._matches((), ConditionPolicy(allow_unknown=False)), [])

    def test_a_rejection_is_how_a_policy_declines_a_stated_fault(self):
        """The strict reading that does not also throw away the good lots."""
        policy = ConditionPolicy(reject=frozenset({"used"}))
        self.assertEqual(self._matches(("used",), policy), [])
        self.assertEqual(self._matches((), policy), ["soundbar"])


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
        scored = evaluate(listing, config, now=READING)
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


class MaximumRetailShareTests(unittest.TestCase):
    """The per-rule ceiling, for a want only worth having as a steal.

    A cost ceiling in dollars cannot say "cheap for what it is": that is a
    proportion, and it has to be rewritten every time the thing comes in a
    different size. This says it once.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _rule(self, **overrides):
        settings = {
            "name": "3d printing",
            "any_terms": ("3d printer",),
            "maximum_retail_ratio": Decimal("0.10"),
        }
        settings.update(overrides)
        return replace(self.config, interests=(InterestRule(**settings),))

    def _matches(self, config, **listing_overrides):
        listing = replace(self.listings[SOUNDBAR], **listing_overrides)
        scored = evaluate(listing, config, now=READING)
        return [item.rule_name for item in scored if item.category == "wanted"]

    def test_a_lot_inside_the_share_is_still_wanted(self):
        # $20 plus the example premium stays under a tenth of $400.
        self.assertEqual(
            self._matches(
                self._rule(),
                title="Creality 3D Printer",
                current_bid=Decimal("20"),
                estimated_retail=Decimal("400"),
            ),
            ["3d printing"],
        )

    def test_a_lot_bid_past_the_share_stops_being_wanted(self):
        self.assertEqual(
            self._matches(
                self._rule(),
                title="Creality 3D Printer",
                current_bid=Decimal("120"),
                estimated_retail=Decimal("400"),
            ),
            [],
        )

    def test_a_lot_stating_no_retail_states_no_share_to_exceed(self):
        # Unlike the floor above, which declines an unproven claim, this only
        # caps a claim that was actually made.
        self.assertEqual(
            self._matches(
                self._rule(),
                title="Creality 3D Printer",
                current_bid=Decimal("120"),
                estimated_retail=None,
            ),
            ["3d printing"],
        )

    def test_a_rule_naming_no_share_is_governed_only_by_the_shared_ceiling(self):
        self.assertEqual(
            self._matches(
                self._rule(maximum_retail_ratio=None),
                title="Creality 3D Printer",
                current_bid=Decimal("120"),
                estimated_retail=Decimal("400"),
            ),
            ["3d printing"],
        )

    def test_a_share_above_all_of_retail_is_refused(self):
        with self.assertRaisesRegex(ValueError, "maximum_retail_ratio"):
            InterestRule(name="x", maximum_retail_ratio=Decimal("1.5"))

    def test_a_negative_share_is_refused(self):
        with self.assertRaisesRegex(ValueError, "maximum_retail_ratio"):
            InterestRule(name="x", maximum_retail_ratio=Decimal("-0.1"))


class WarehouseNoteTests(unittest.TestCase):
    """What a warehouse note may and may not do to a match.

    A title is the manufacturer's words and is the same on every copy of a
    product. The note is what somebody wrote after looking at this actual lot,
    so it is the only place a missing blower or a leaking seam is ever said.

    Reading a note *against* a lot is the judge's job now, and those tests
    live in test_judging. What stays here is the half that is a property of
    matching itself: a note can never put a lot into the report, whoever is
    reading it.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _matches(self, *, notes="", title="Inflatable Water Slide Bounce House"):
        rule = InterestRule(name="bounce house", any_terms=("water slide",))
        listing = replace(
            self.listings[SOUNDBAR],
            title=title,
            notes=notes,
            estimated_retail=Decimal("300"),
        )
        config = replace(self.config, interests=(rule,))
        scored = evaluate(listing, config, now=READING)
        return [item.rule_name for item in scored if item.category == "wanted"]

    def test_a_note_leaves_a_lot_its_title_already_matched_alone(self):
        # Matching reads the title. Whatever the note says, ruling this lot
        # out is a judgement, and judgement happens after matching.
        self.assertEqual(self._matches(notes="updated 9/8"), ["bounce house"])
        self.assertEqual(
            self._matches(notes="9/8 blower not included"), ["bounce house"]
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
        scored = evaluate(listing, config, now=READING)
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

    def test_a_capacity_after_for_does_not_make_a_thing_its_own_accessory(self):
        # Found by diffing the rules against a local model over ten thousand
        # real titles. This is a camping tent, and the only "for" in it says
        # how many people fit. Reading that as a host made the tent an
        # accessory to itself, and the lot was never reported.
        rule = InterestRule(name="camping tent", any_terms=("instant cabin",))
        self.assertEqual(
            self._matches(
                "Blackout Camping Tent for 6 Person, Instant Cabin Tents with Rainfly",
                rule=rule,
            ),
            ["camping tent"],
        )

    def test_a_host_named_after_a_capacity_is_still_a_host(self):
        # The number must not become a way to smuggle an accessory through:
        # once a real product follows a later "for", the question is asked of
        # that one instead.
        rule = InterestRule(name="tools", any_terms=("dewalt",))
        self.assertEqual(
            self._matches(
                "Weed Wacker Kit for 2 Batteries, for DeWalt 20V Cordless", rule=rule
            ),
            [],
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
        defaults = InterestDefaults(accessory_nouns=("stand",))
        rule = defaults.applied_to(
            InterestRule(name="guitar stand", any_terms=("guitar stand",))
        )
        self.assertEqual(rule.accessory_nouns, ())
        self.assertEqual(self._matches("DIDA Guitar Stand", rule=rule), ["guitar stand"])

    def test_a_rule_inherits_accessory_words_it_never_named(self):
        rule = InterestDefaults(accessory_nouns=("hanger",)).applied_to(
            InterestRule(name="guitar", any_terms=("guitar",))
        )
        self.assertIn("hanger", rule.accessory_nouns)
        self.assertEqual(self._matches("Guitar Wall Hanger 3 Pack", rule=rule), [])


class DeclinedTermTests(unittest.TestCase):
    """A broad recall term has to be paired with a way to say "not that one".

    Measured against one real capture, nine of nine lots matched by a rule
    whose only term was "monitor" were refused by the judge, every one of them
    correctly: a doorbell, two blood pressure cuffs, a baby monitor, a security
    camera. The rule was right about what it wanted and had no way to say so
    before scoring, so the same five products were found, scored, asked about
    and demoted every single run.
    """

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _matches(self, title, rule):
        config = replace(self.config, interests=(rule,))
        listing = replace(
            self.listings[SOUNDBAR], title=title, estimated_retail=Decimal("300")
        )
        scored = evaluate(listing, config, now=READING)
        return [item.rule_name for item in scored if item.category == "wanted"]

    def _monitor_rule(self):
        return InterestRule(
            name="monitor",
            any_terms=("monitor",),
            none_terms=("blood pressure", "baby monitor"),
        )

    def test_a_declined_word_removes_the_lot(self):
        self.assertEqual(
            self._matches("Brightwell Blood Pressure Monitor for Home", self._monitor_rule()),
            [],
        )

    def test_the_thing_itself_still_matches(self):
        self.assertEqual(
            self._matches("Northvale NV34UW 34in UltraWide Monitor", self._monitor_rule()),
            ["monitor"],
        )

    def test_position_does_not_matter(self):
        """Unlike an accessory word, which is only an accessory when adjacent."""
        self.assertEqual(
            self._matches("Monitor, Automatic Blood Pressure", self._monitor_rule()), []
        )

    def test_a_longer_word_that_merely_begins_the_same_is_not_it(self):
        rule = InterestRule(name="tent", any_terms=("tent",), none_terms=("pet",))
        self.assertEqual(
            self._matches("Tallgrass 8-Person Tent, Petite Pack", rule), ["tent"]
        )

    def test_a_plural_of_a_declined_word_is_declined(self):
        rule = InterestRule(name="aquarium", any_terms=("aquarium",), none_terms=("pond",))
        self.assertEqual(
            self._matches("Collapsible Garden Fish Ponds with Aquarium Liner", rule), []
        )

    def test_a_rule_that_declines_nothing_is_unchanged(self):
        rule = InterestRule(name="monitor", any_terms=("monitor",))
        self.assertEqual(
            self._matches("Brightwell Blood Pressure Monitor for Home", rule), ["monitor"]
        )

    def test_a_declined_word_outranks_a_required_one(self):
        """Both lists can be satisfied at once, and the refusal has to win."""
        rule = InterestRule(
            name="monitor",
            all_terms=("monitor",),
            none_terms=("blood pressure",),
        )
        self.assertEqual(
            self._matches("Blood Pressure Monitor, Upper Arm", rule), []
        )


class InterestWeightTests(unittest.TestCase):
    """Weight decides what is read first, never what is allowed through."""

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_a_wanted_lot_outranks_a_bargain_nobody_asked_for(self):
        scored = evaluate(self.listings[SOUNDBAR], self.config, now=READING)
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
        candidates = evaluate(self.listings[LASER_LEVEL], config, now=READING)
        anomaly = next(item for item in candidates if item.category == "anomaly")
        self.assertEqual(anomaly.priority, 0)
        self.assertGreaterEqual(anomaly.score, config.scoring.minimum_report_score)

    def test_raising_a_weight_cannot_push_a_lot_past_a_bar_it_failed(self):
        eager = InterestRule(name="soundbar", any_terms=("sound bar",), minimum_score=99,
                             weight=Decimal("10"))
        config = replace(self.config, interests=(eager,))
        scored = evaluate(self.listings[SOUNDBAR], config, now=READING)
        self.assertEqual([item for item in scored if item.category == "wanted"], [])

    def test_a_negative_weight_is_refused(self):
        with self.assertRaisesRegex(ValueError, "weight"):
            InterestRule(name="anything", weight=Decimal("-1"))


class AnomalyScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def test_deep_discount_on_a_valuable_item_is_reported(self):
        candidate = self._anomaly(
            evaluate(self.listings[LASER_LEVEL], self.config, now=READING)
        )
        self.assertEqual(candidate.rule_name, "retail-ratio")
        self.assertIn("of stated retail", candidate.reasons[0])

    def test_retail_below_the_floor_is_not_an_anomaly(self):
        listing = replace(self.listings[LASER_LEVEL], estimated_retail=Decimal("60.00"))
        self.assertEqual(evaluate(listing, self.config, now=READING), [])

    def test_listing_without_retail_is_not_an_anomaly(self):
        listing = replace(self.listings[LASER_LEVEL], estimated_retail=None)
        self.assertEqual(evaluate(listing, self.config, now=READING), [])

    def test_freshness_alone_cannot_push_an_anomaly_past_the_global_bar(self):
        listing = self.listings[LASER_LEVEL]
        ordinary = self._anomaly(
            evaluate(listing, self.config, ObservationChange(False, False), now=READING)
        )
        config = replace(
            self.config,
            scoring=replace(
                self.config.scoring,
                minimum_report_score=ordinary.score + 1,
            ),
        )

        self.assertEqual(
            evaluate(listing, config, ObservationChange(True, False), now=READING), []
        )

    def _anomaly(self, candidates):
        return next(item for item in candidates if item.category == "anomaly")


class UntestedPriceTests(unittest.TestCase):
    """Hours before a lot closes, its bid is the opening price, not a discount.

    Before bidding starts a lot's total sits near zero, so its share of stated
    retail is near zero and the discount score is near full marks -- for every
    lot in the catalogue alike. Measured on recorded history, the ceiling
    admitted all 3,064 lots seen with no bids at least six hours out, which is
    a test nothing can fail. These pin the clock as the cure, and pin that it
    reaches only the bargain claim.
    """

    # The unasked fixture states no end time, which is its own case below, so
    # the timing tests give it one relative to a fixed reading.
    NOW = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()
        self.unasked = self.listings[LASER_LEVEL]

    def _ending_in(self, minutes, listing=None):
        return replace(
            listing or self.unasked, ends_at=self.NOW + timedelta(minutes=minutes)
        )

    def _categories(self, listing, config=None):
        found = evaluate(listing, config or self.config, now=self.NOW)
        return {item.category for item in found}

    def test_an_unasked_lot_far_from_closing_is_not_called_cheap(self):
        """The complaint, exactly: nobody has bid and it ends in 23 hours."""
        self.assertEqual(
            evaluate(self._ending_in(60 * 23), self.config, now=self.NOW), []
        )

    def test_the_same_lot_near_its_close_is(self):
        self.assertIn("anomaly", self._categories(self._ending_in(10)))

    def test_the_boundary_itself_counts_as_tested(self):
        window = self.config.scoring.anomaly_within_minutes

        self.assertIn("anomaly", self._categories(self._ending_in(window)))
        self.assertEqual(
            evaluate(self._ending_in(window + 1), self.config, now=self.NOW), []
        )

    def test_a_want_survives_the_gate_that_retires_the_bargain_claim(self):
        """Evidence may retire a bargain claim, never a request.

        Three hours out is deliberately between the two bars: past the point
        where a discount means anything, well inside the hours a request is
        still worth reading. That gap is the whole point of their being two.
        """
        wanted_but_untested = self._ending_in(60 * 3, self.listings[SOUNDBAR])
        categories = self._categories(wanted_but_untested)

        self.assertIn("wanted", categories)
        self.assertNotIn("anomaly", categories)

    def test_a_lot_that_never_stated_an_end_is_still_eligible(self):
        """No clock to wait for means the fault cannot apply."""
        self.assertIsNone(self.unasked.ends_at)
        self.assertIn("anomaly", self._categories(self.unasked))

    def test_zero_turns_the_gate_off(self):
        config = replace(
            self.config,
            scoring=replace(self.config.scoring, anomaly_within_minutes=0),
        )

        self.assertIn("anomaly", self._categories(self._ending_in(60 * 23), config))

    def test_the_window_is_the_configured_one(self):
        config = replace(
            self.config,
            scoring=replace(self.config.scoring, anomaly_within_minutes=60 * 24),
        )

        self.assertIn("anomaly", self._categories(self._ending_in(60 * 23), config))

    def test_the_gate_does_not_change_what_a_reported_bargain_scores(self):
        """It decides whether to speak, not how loudly."""
        listing = self._ending_in(10)
        ungated_config = replace(
            self.config,
            scoring=replace(self.config.scoring, anomaly_within_minutes=0),
        )

        self.assertEqual(
            self._anomaly_in(listing, self.config).score,
            self._anomaly_in(listing, ungated_config).score,
        )

    def test_a_want_a_day_out_is_held_until_its_closing_day(self):
        """The second complaint: a want mailed the night before it closes.

        Its bid is zero, so the card's total reads zero, which tells a reader
        the thing exists and nothing about what it will cost. The lot is not
        refused -- the passes on its own closing day reach it with hours left.
        """
        self.assertEqual(
            evaluate(
                self._ending_in(60 * 23, self.listings[SOUNDBAR]),
                self.config,
                now=self.NOW,
            ),
            [],
        )

    def test_a_want_closing_later_the_same_day_is_read_today(self):
        """Ten hours out and still today, so there is nothing to wait for."""
        tonight = datetime(2026, 10, 13, 21, 0, tzinfo=self._local())
        soundbar = replace(self.listings[SOUNDBAR], ends_at=tonight)

        self.assertIn("wanted", self._categories(soundbar))

    def test_the_boundary_is_the_local_day_and_not_a_count_of_hours(self):
        """Thirteen hours away, but on the next local day, so it waits.

        This is the edge the default accepts, and the record is what makes it
        safe: no lot has ever closed between 22:00 and 18:00, so the midnight
        line falls in an empty stretch instead of through an evening's lots.
        Reading it in the provider's zone rather than UTC is the whole point --
        in UTC this close and this reading are the same day.
        """
        just_after_midnight = datetime(2026, 10, 14, 0, 30, tzinfo=self._local())
        soundbar = replace(self.listings[SOUNDBAR], ends_at=just_after_midnight)

        self.assertEqual(evaluate(soundbar, self.config, now=self.NOW), [])

    def test_lead_days_open_the_wanted_bar_without_touching_the_bargain_one(self):
        """Loosening one must not loosen the other."""
        reads_ahead = replace(
            self.config,
            scoring=replace(self.config.scoring, wanted_lead_days=1),
        )
        distant = self._ending_in(60 * 23, self.listings[SOUNDBAR])

        categories = self._categories(distant, reads_ahead)
        self.assertIn("wanted", categories)
        self.assertNotIn("anomaly", categories)

    def _local(self):
        return self.config.acquisition.zone

    def test_a_wanted_lot_with_no_stated_end_is_still_reported(self):
        """No clock to wait for, so there is nothing to defer it until."""
        endless = replace(self.listings[SOUNDBAR], ends_at=None)

        self.assertIn("wanted", self._categories(endless))

    def test_the_wanted_bar_does_not_change_what_a_want_scores(self):
        """It decides whether to speak, not how loudly."""
        soundbar = self._ending_in(60, self.listings[SOUNDBAR])
        ungated = replace(
            self.config,
            scoring=replace(self.config.scoring, wanted_lead_days=30),
        )

        self.assertEqual(
            self._wanted_in(soundbar, self.config).score,
            self._wanted_in(soundbar, ungated).score,
        )

    def _wanted_in(self, listing, config):
        return next(
            item
            for item in evaluate(listing, config, now=self.NOW)
            if item.category == "wanted"
        )

    def _anomaly_in(self, listing, config):
        return next(
            item
            for item in evaluate(listing, config, now=self.NOW)
            if item.category == "anomaly"
        )


class LargeLotBandTests(unittest.TestCase):
    """A big thing at a fair price has no way in through the one ratio."""

    def setUp(self):
        self.config = example_config()
        self.listings = example_listings()

    def _fair_priced_big_lot(self):
        """Priced at a third of retail: a good buy, nowhere near a steal."""
        return replace(
            self.listings[LASER_LEVEL],
            estimated_retail=Decimal("1000.00"),
            current_bid=Decimal("290.00"),
        )

    def _with_band(self, retail, ratio):
        return replace(
            self.config,
            scoring=replace(
                self.config.scoring,
                large_lot_minimum_retail=Decimal(retail),
                large_lot_maximum_ratio=Decimal(ratio),
            ),
        )

    def test_a_fair_price_on_a_big_lot_is_refused_without_the_band(self):
        self.assertEqual(evaluate(self._fair_priced_big_lot(), self.config, now=READING), [])

    def test_the_band_lets_a_fair_price_on_a_big_lot_through(self):
        config = self._with_band("800", "0.40")
        candidate = self._anomaly(evaluate(self._fair_priced_big_lot(), config, now=READING))
        self.assertEqual(candidate.rule_name, "retail-ratio")

    def test_a_lot_under_the_band_still_answers_to_the_ordinary_ratio(self):
        """The band is a second door, never a replacement for the first."""
        config = self._with_band("800", "0.40")
        smaller = replace(
            self.listings[LASER_LEVEL],
            estimated_retail=Decimal("400.00"),
            current_bid=Decimal("120.00"),
        )
        self.assertEqual(evaluate(smaller, config, now=READING), [])

    def test_the_band_does_not_admit_a_big_lot_at_any_price(self):
        config = self._with_band("800", "0.40")
        expensive = replace(
            self.listings[LASER_LEVEL],
            estimated_retail=Decimal("1000.00"),
            current_bid=Decimal("600.00"),
        )
        self.assertEqual(evaluate(expensive, config, now=READING), [])

    def test_a_generous_band_is_not_vetoed_by_the_report_floor(self):
        """The trap this band had to be designed around.

        The anomaly score is the discount itself, so a ceiling of 40% of
        retail can only ever score 60. A report floor of 70 would refuse
        every lot the band admitted, and the band would look broken while
        being configured correctly.
        """
        config = self._with_band("800", "0.40")
        config = replace(
            config, scoring=replace(config.scoring, minimum_report_score=70)
        )
        candidate = self._anomaly(evaluate(self._fair_priced_big_lot(), config, now=READING))
        self.assertLess(candidate.score, 70)

    def test_the_ordinary_band_keeps_a_floor_set_above_its_own_ceiling(self):
        """Relaxing the floor for the large band must not relax it for all.

        A floor above what a bare discount can score is a deliberate
        statement that a discount alone is not enough.
        """
        config = self._with_band("800", "0.40")
        ordinary = self._anomaly(evaluate(self.listings[LASER_LEVEL], config, now=READING))
        strict = replace(
            config,
            scoring=replace(
                config.scoring, minimum_report_score=ordinary.score + 1
            ),
        )
        self.assertEqual(evaluate(self.listings[LASER_LEVEL], strict, now=READING), [])

    def test_a_band_cheaper_than_the_ordinary_one_is_refused(self):
        with self.assertRaisesRegex(ValueError, "large_lot_minimum_retail"):
            ScoringConfig(
                anomaly_minimum_retail=Decimal("500"),
                large_lot_minimum_retail=Decimal("100"),
            )

    def test_a_negative_ending_soon_window_is_refused(self):
        """Not about the band. It is here because the band deleted this check.

        Inserting the band's methods after __post_init__ left this validation
        stranded below a return statement, where it could never run, and all
        786 tests still passed because nothing asked for it. A validator with
        no test is a validator that can be removed by accident.
        """
        with self.assertRaisesRegex(ValueError, "ending_soon_minutes"):
            ScoringConfig(ending_soon_minutes=-1)

    def test_a_band_stricter_than_the_ordinary_one_is_refused(self):
        """A second band exists to be more generous, never less."""
        with self.assertRaisesRegex(ValueError, "large_lot_maximum_ratio"):
            ScoringConfig(
                anomaly_maximum_ratio=Decimal("0.30"),
                large_lot_minimum_retail=Decimal("800"),
                large_lot_maximum_ratio=Decimal("0.20"),
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
            for item in evaluate(listing, self.config, logistics_decision=feasible, now=READING)
            if item.category == "wanted"
        )
        self.assertEqual(candidate.total_cost, Decimal("30.70"))
        self.assertEqual(candidate.logistics.status, "feasible")

        too_expensive = replace(feasible, added_cost=Decimal("40"))
        self.assertEqual(
            evaluate(
                listing, self.config, logistics_decision=too_expensive, now=READING
            ),
            [],
        )

    def test_infeasible_decision_suppresses_the_listing_entirely(self):
        listing = replace(self.listings[SOUNDBAR], handling_weight_lb=Decimal("148"))
        decision = LogisticsDecision(status="infeasible")
        self.assertEqual(
            evaluate(
                listing, self.config, logistics_decision=decision, now=READING
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
