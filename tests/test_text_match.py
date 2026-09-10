"""Whole-word term matching, written from the false positives that motivated it."""

from __future__ import annotations

import unittest

from auction_lens.text_match import first_mention, mentions, standalone_mentions


class WholeWordTests(unittest.TestCase):
    def test_it_finds_the_term_standing_on_its_own(self):
        self.assertTrue(mentions("metal shed with floor", "shed"))
        self.assertTrue(mentions("shed", "shed"))
        self.assertTrue(mentions("a bounce house slide", "bounce house"))

    def test_it_refuses_a_term_buried_in_a_longer_word(self):
        # Every one of these matched a rule before this module existed.
        self.assertFalse(mentions("polished chrome bull bar", "shed"))
        self.assertFalse(mentions("brushed nickel faucet", "shed"))
        self.assertFalse(mentions("petgrow artificial grass", "petg"))
        self.assertFalse(mentions("weight monitoring litter box", "monitor"))

    def test_a_plural_is_still_the_word(self):
        self.assertTrue(mentions("two metal sheds", "shed"))
        self.assertTrue(mentions("four bounce houses", "bounce house"))
        self.assertTrue(mentions("glass boxes", "box"))

    def test_punctuation_and_edges_count_as_boundaries(self):
        self.assertTrue(mentions("shed, 10x12", "shed"))
        self.assertTrue(mentions("(shed)", "shed"))
        self.assertTrue(mentions("storage-shed", "shed"))
        self.assertTrue(mentions("outdoor shed", "shed"))

    def test_a_digit_running_on_is_not_a_boundary(self):
        self.assertFalse(mentions("4k60 capture card", "4k"))
        self.assertTrue(mentions("4k monitor", "4k"))

    def test_it_reports_where_the_term_stands_alone(self):
        # The buried "shed" in "polished" comes first in the string; the real
        # one is later, and it is the later one the position must point at.
        text = "polished rail and a shed"
        self.assertEqual(first_mention(text, "shed"), text.rindex("shed"))
        self.assertEqual(first_mention("polished only", "shed"), -1)

    def test_it_yields_every_standalone_position_in_order(self):
        text = "shed beside a polished rail beside a shed"
        self.assertEqual(
            list(standalone_mentions(text, "shed")), [0, text.rindex("shed")]
        )


if __name__ == "__main__":
    unittest.main()
