"""The operator's own ceiling, read from a title and printed beside the bid.

Two things matter and neither would be loud if it broke. A size read where no
size was written raises a ceiling nobody chose -- the 10 in "10-in-1" is a
count of toys, not ten inches. And a ceiling that moved a score would turn the
operator's price into evidence, which it is not; it is only ever shown.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from auction_lens.config.hammer import HammerRubric, HammerTier
from auction_lens.config.load import parse_config
from auction_lens.matching.evaluate import evaluate
from auction_lens.matching.hammer import ceiling_for, measured_in
from auction_lens.reports.findings import build_report
from auction_lens.reports.html import render_html
from auction_lens.reports.text import render_text
from support import REPORT_ZONE, SOUNDBAR, example_listings, shortly_before_close

_CONFIG = """
[provider]
provider_id = "nellis"

[locations]
far = ["far town"]
far_minimum_score = 0

[[interests]]
name = "water slide"
wants = "an inflatable water slide"
any_terms = ["water slide"]

[interests.max_hammer]
base = 20
tiers = [
    { feet = 12, hammer = 25 },
    { pounds = 300, hammer = 25 },
    { feet = 15, hammer = 30 },
]
less = { "used" = 5 }
at_most = { "untested" = 8 }
walk = ["no blower", "minor damage"]
walk_if_far = true
"""

RUBRIC = HammerRubric(
    base=Decimal("20"),
    tiers=(
        HammerTier(hammer=Decimal("25"), feet=Decimal("12")),
        HammerTier(hammer=Decimal("25"), pounds=Decimal("300")),
        HammerTier(hammer=Decimal("30"), feet=Decimal("15")),
    ),
    less={"used": Decimal("5")},
    at_most={"untested": Decimal("8")},
    walk=("no blower", "minor damage"),
    walk_if_far=True,
)


def _listing(title, **changes):
    """The shared fixture with its own grade removed, so each test states one."""
    clean = replace(example_listings()[SOUNDBAR], conditions=(), grade=None)
    return replace(clean, title=title, **changes)


def _ceiling(title, conditions=(), *, is_far=False, **changes):
    listing = _listing(title, **changes)
    return ceiling_for(listing, frozenset(conditions), RUBRIC, is_far=is_far)


class MeasuredTests(unittest.TestCase):
    """A size is a number with a unit, and nothing else is."""

    def test_sides_sharing_one_unit_are_all_read_and_the_longest_kept(self):
        self.assertEqual(measured_in("Acme 12.4x11FT Water Slide").feet, Decimal("12.4"))

    def test_inches_are_converted_to_feet(self):
        self.assertEqual(measured_in("Acme Slide 150 x 120 x 90 in").feet, Decimal("12.5"))

    def test_a_side_labelled_where_it_is_written_still_counts(self):
        self.assertEqual(measured_in("Acme 14.4ft L x 9.1ft W").feet, Decimal("14.4"))

    def test_a_count_of_toys_is_not_a_length(self):
        self.assertIsNone(measured_in("Acme 10 in 1 Water Slide").feet)
        self.assertIsNone(measured_in("Acme 10-in-1 Water Slide").feet)

    def test_a_number_with_no_unit_is_not_a_size(self):
        self.assertFalse(measured_in("Acme Water Slide for Ages 3-9").is_stated)

    def test_a_weight_is_read_in_pounds(self):
        self.assertEqual(measured_in("Acme Slide (450lbs Capacity)").pounds, Decimal("450"))


class CeilingTests(unittest.TestCase):
    """The rubric applied in its order: tier, then condition, then faults."""

    def test_a_title_stating_no_size_gets_the_base(self):
        ceiling = _ceiling("Acme Water Slide")

        self.assertEqual(ceiling.hammer, Decimal("20"))
        self.assertFalse(ceiling.measured.is_stated)

    def test_the_highest_tier_the_title_reaches_decides(self):
        self.assertEqual(_ceiling("Acme 16ft Water Slide").hammer, Decimal("30"))
        self.assertEqual(_ceiling("Acme 13ft Water Slide").hammer, Decimal("25"))

    def test_either_measurement_can_lift_it(self):
        self.assertEqual(_ceiling("Acme 10ft Slide, 320 lb").hammer, Decimal("25"))

    def test_a_used_copy_is_worth_less_by_the_stated_amount(self):
        ceiling = _ceiling("Acme 16ft Water Slide", ("used",))

        self.assertEqual(ceiling.hammer, Decimal("25"))
        self.assertEqual(ceiling.adjustments, ("used -$5",))

    def test_an_unchecked_copy_is_capped_whatever_its_size(self):
        self.assertEqual(
            _ceiling("Acme 16ft Water Slide", ("used", "untested")).hammer, Decimal("8")
        )

    def test_a_fault_in_the_warehouse_note_withdraws_the_figure(self):
        ceiling = _ceiling("Acme 16ft Water Slide", notes="Blower not included. No blower.")

        self.assertIsNone(ceiling.hammer)
        self.assertEqual(ceiling.walk, "no blower")

    def test_a_fault_in_the_grade_withdraws_it_too(self):
        self.assertEqual(_ceiling("Acme Slide", ("minor damage",)).walk, "minor damage")

    def test_a_far_branch_withdraws_it_when_the_rubric_says_so(self):
        self.assertEqual(_ceiling("Acme Slide", is_far=True).walk, "far branch")
        near_only = replace(RUBRIC, walk_if_far=False)
        listing = _listing("Acme Slide")
        self.assertEqual(
            ceiling_for(listing, frozenset(), near_only, is_far=True).hammer,
            Decimal("20"),
        )


class ConfiguredRubricTests(unittest.TestCase):
    def test_the_table_is_read_onto_its_rule(self):
        (rule,) = parse_config(_CONFIG).interests

        self.assertEqual(rule.max_hammer, RUBRIC)

    def test_a_rule_without_one_has_none(self):
        config = parse_config(_CONFIG.split("[interests.max_hammer]")[0])

        self.assertIsNone(config.interests[0].max_hammer)

    def test_a_tier_must_name_exactly_one_measurement(self):
        broken = _CONFIG.replace("{ feet = 12, hammer = 25 }", "{ hammer = 25 }")

        with self.assertRaises(ValueError) as caught:
            parse_config(broken)
        self.assertIn("max_hammer.tiers[0]", str(caught.exception))

    def test_a_rubric_with_no_base_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            parse_config(_CONFIG.replace("base = 20\n", ""))
        self.assertIn("max_hammer.base", str(caught.exception))


class ReportedCeilingTests(unittest.TestCase):
    """The figure reaches the card, with its working, and changes no score."""

    def _candidate(self, title, bid, **changes):
        config = parse_config(_CONFIG)
        listing = _listing(title, current_bid=Decimal(bid), **changes)
        (candidate,) = [
            found
            for found in evaluate(listing, config, now=shortly_before_close())
            if found.rule_name == "water slide"
        ]
        return candidate

    def test_the_card_shows_the_figure_and_the_size_it_came_from(self):
        candidate = self._candidate("Acme 16ft Water Slide", "12")

        rendered = render_text(build_report([candidate], REPORT_ZONE))
        self.assertIn("Max hammer: $30 (16 ft)", rendered)
        self.assertNotIn("past it", rendered)

    def test_a_bid_already_past_it_says_walk(self):
        candidate = self._candidate("Acme 16ft Water Slide", "41")

        rendered = render_html(build_report([candidate], REPORT_ZONE))
        self.assertIn("$30 (16 ft) -- the bid is already past it; walk", rendered)

    def test_a_far_lot_is_told_to_walk(self):
        candidate = self._candidate("Acme Water Slide", "5", location="Far Town")

        rendered = render_text(build_report([candidate], REPORT_ZONE))
        self.assertIn("Max hammer: walk (far branch)", rendered)

    def test_the_ceiling_moves_no_score(self):
        with_rubric = self._candidate("Acme 16ft Water Slide", "41")
        config = parse_config(_CONFIG.split("[interests.max_hammer]")[0])
        listing = _listing("Acme 16ft Water Slide", current_bid=Decimal("41"))
        (without,) = [
            found
            for found in evaluate(listing, config, now=shortly_before_close())
            if found.rule_name == "water slide"
        ]

        self.assertEqual(with_rubric.score, without.score)
        self.assertIsNone(without.max_hammer)


if __name__ == "__main__":
    unittest.main()
