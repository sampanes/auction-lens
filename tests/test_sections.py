"""One section per kind of thing, and what each section admits it left out.

A report that shows thirty lots can still be useless if ten of them are the
same keyboard. These tests hold the two halves of the fix together: no one
interest may spend the page, and whatever is held back is counted and reachable
rather than quietly gone.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from auction_lens.config import parse_config
from auction_lens.models import (
    Candidate,
    CandidateCategory,
    InterestHarvest,
    ObservationChange,
    best_of_each,
    harvest_of,
)
from auction_lens.reporting import build_report, render_html, render_text
from auction_lens.reporting.searches import SearchHint
from support import EXAMPLE_CONFIG, REPORT_ZONE, SOUNDBAR, example_listings

NO_CHANGE = ObservationChange(is_new=False, price_changed=False)


def candidate(rule: str, score: int, *, category=CandidateCategory.WANTED) -> Candidate:
    """One scored match, saying only what these tests are about."""
    return Candidate(
        listing=replace(
            example_listings()[SOUNDBAR],
            listing_id=f"{rule}-{score}",
            title=f"{rule} at {score}",
        ),
        category=category,
        rule_id=rule,
        rule_name=rule if category == CandidateCategory.WANTED else "",
        score=score,
        total_cost=Decimal("10"),
        retail_ratio=Decimal("0.1"),
        reasons=(f"matches {rule}",),
        change=NO_CHANGE,
    )


def many(rule: str, scores: list[int]) -> list[Candidate]:
    return [candidate(rule, score) for score in scores]


class SectionNameTests(unittest.TestCase):
    def test_a_match_is_one_of_its_interest(self):
        self.assertEqual(candidate("telescope", 80).section, "telescope")

    def test_a_lot_reported_on_price_alone_is_one_of_that_reason(self):
        priced = candidate("", 95, category=CandidateCategory.ANOMALY)
        self.assertEqual(priced.section, "anomaly")


class BestOfEachTests(unittest.TestCase):
    def test_one_crowded_interest_cannot_spend_the_whole_report(self):
        crowd = many("keyboard", [87, 86, 85, 84, 83, 82, 81])
        others = many("telescope", [80]) + many("guitar", [79])
        kept = best_of_each(crowd + others, 3)
        self.assertEqual(len([item for item in kept if item.section == "keyboard"]), 3)
        self.assertEqual(len(kept), 5)

    def test_the_few_kept_are_the_best_few(self):
        kept = best_of_each(many("keyboard", [70, 87, 75, 86, 85]), 3)
        self.assertEqual(sorted(item.score for item in kept), [85, 86, 87])

    def test_an_interest_below_the_limit_is_untouched(self):
        kept = best_of_each(many("telescope", [80, 79]), 3)
        self.assertEqual(len(kept), 2)

    def test_the_result_is_still_in_reading_order(self):
        kept = best_of_each(many("keyboard", [80, 87]) + many("guitar", [83]), 3)
        self.assertEqual([item.score for item in kept], [87, 83, 80])


class HarvestTests(unittest.TestCase):
    def test_it_reports_what_matched_beside_what_is_shown(self):
        found = many("keyboard", [87, 86, 85, 84]) + many("telescope", [80])
        shown = best_of_each(found, 3)
        harvest = {tally.name: tally for tally in harvest_of(found, shown)}
        self.assertEqual((harvest["keyboard"].found, harvest["keyboard"].shown), (4, 3))
        self.assertEqual(harvest["keyboard"].withheld, 1)
        self.assertTrue(harvest["keyboard"].is_crowded)

    def test_an_interest_shown_in_full_is_not_crowded(self):
        found = many("telescope", [80, 79])
        harvest = harvest_of(found, found)
        self.assertEqual(harvest[0].withheld, 0)
        self.assertFalse(harvest[0].is_crowded)


class SectionedReportTests(unittest.TestCase):
    def setUp(self):
        self.found = many("keyboard", [87, 86, 85, 84, 83]) + many("telescope", [80])
        self.shown = best_of_each(self.found, 3)
        self.harvest = harvest_of(self.found, self.shown)
        self.hints = (
            SearchHint(rule="keyboard", phrase="88 key", finds=5, also_finds=12),
            SearchHint(rule="telescope", phrase="telescope", finds=1, also_finds=3),
        )

    def _report(self):
        return build_report(
            self.shown, REPORT_ZONE, self.hints, harvest=self.harvest
        )

    def test_each_kind_gets_its_own_section(self):
        titles = [group.title for group in self._report().groups]
        self.assertEqual(titles, ["Keyboard", "Telescope"])

    def test_a_crowded_section_says_how_many_it_is_not_showing(self):
        keyboard = self._report().groups[0]
        self.assertEqual(keyboard.withheld, 2)
        self.assertTrue(keyboard.is_crowded)

    def test_a_crowded_section_carries_only_its_own_phrase(self):
        keyboard = self._report().groups[0]
        self.assertEqual([hint.phrase for hint in keyboard.searches], ["88 key"])

    def test_a_section_showing_everything_offers_no_shortcut(self):
        # The phrase is a way past a list too long to click through. There is
        # no list to get past here, so offering one would only add noise.
        telescope = self._report().groups[1]
        self.assertEqual(telescope.searches, ())

    def test_a_phrase_no_section_claimed_still_reaches_the_footer(self):
        orphan = SearchHint(rule="retired want", phrase="kayak", finds=4, also_finds=1)
        report = build_report(
            self.shown, REPORT_ZONE, (*self.hints, orphan), harvest=self.harvest
        )
        self.assertEqual([hint.phrase for hint in report.searches], ["kayak"])


class SectionRenderingTests(unittest.TestCase):
    def setUp(self):
        found = many("keyboard", [87, 86, 85, 84, 83]) + many("telescope", [80])
        self.shown = best_of_each(found, 3)
        self.harvest = harvest_of(found, self.shown)
        self.hints = (
            SearchHint(rule="keyboard", phrase="88 key", finds=5, also_finds=12),
        )

    def _text(self):
        return render_text(
            self.shown, REPORT_ZONE, self.hints, harvest=self.harvest
        )

    def _html(self):
        return render_html(
            self.shown, REPORT_ZONE, self.hints, harvest=self.harvest
        )

    def test_text_names_every_section(self):
        report = self._text()
        self.assertIn("KEYBOARD", report)
        self.assertIn("TELESCOPE", report)

    def test_text_admits_what_a_crowded_section_withheld(self):
        self.assertIn("2 more not shown.", self._text())

    def test_text_offers_the_phrase_with_its_honest_cost(self):
        self.assertIn("Search: 88 key | finds 5, plus 12 other lot(s)", self._text())

    def test_text_says_nothing_extra_for_a_section_shown_in_full(self):
        # One "more not shown" line, from the keyboards, and none for the one
        # section that is complete.
        self.assertEqual(self._text().count("more not shown"), 1)

    def test_html_puts_the_phrase_in_a_section_as_copyable_text(self):
        markup = self._html()
        self.assertIn("2 more not shown.", markup)
        self.assertIn("<code>88 key</code>", markup)

    def test_html_does_not_link_the_phrase(self):
        # Arriving at the search with the words in the box is the point; a
        # link would arrive at a fixed result nobody can edit.
        self.assertNotIn("<a href='88 key'", self._html())


class HarvestlessReportTests(unittest.TestCase):
    """A report built without a harvest still reads correctly."""

    def test_sections_claim_nothing_is_withheld(self):
        shown = many("telescope", [80, 79])
        report = build_report(shown, REPORT_ZONE)
        self.assertEqual(report.groups[0].withheld, 0)
        self.assertNotIn("more not shown", render_text(shown, REPORT_ZONE))


class InterestHarvestRecordTests(unittest.TestCase):
    def test_withheld_is_what_matched_less_what_is_printed(self):
        self.assertEqual(InterestHarvest("keyboard", found=11, shown=3).withheld, 8)


class MostPerInterestSettingTests(unittest.TestCase):
    """The one number an operator turns, read from the file they already edit."""

    def _parsed(self, line: str = ""):
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
        if line:
            text = text.replace("[reports]", f"[reports]\n{line}", 1)
        return parse_config(text)

    def test_a_file_that_says_nothing_still_limits_a_crowded_interest(self):
        # The crowding fix has to work for someone who never reads this key.
        self.assertEqual(self._parsed().reports.most_per_interest, 3)

    def test_the_file_can_overrule_it(self):
        self.assertEqual(
            self._parsed("most_per_interest = 5").reports.most_per_interest, 5
        )

    def test_zero_is_refused_rather_than_read_as_showing_none(self):
        with self.assertRaises(ValueError) as refused:
            self._parsed("most_per_interest = 0")
        self.assertIn("must be at least 1", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
