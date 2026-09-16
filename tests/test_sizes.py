"""Reading a size and an audience out of a title, and deciding who it fits."""

from __future__ import annotations

import unittest

from auction_lens.matching.model import Person
from auction_lens.matching.sizes import canonical_size, fits, sizes_in, styles_in


def wears(sizes=(), styles=()):
    """One person, written the short way these tests need them."""
    return Person("someone", frozenset(sizes), frozenset(styles))


class ReadingSizesTests(unittest.TestCase):
    def test_a_size_in_its_own_clause_is_read(self):
        self.assertEqual(sizes_in("mens hooded riding shirt - large"), {"letter": {"l"}})

    def test_spellings_of_one_size_all_become_the_same_answer(self):
        for written in ("x-large", "xlarge", "extra large", "xl"):
            with self.subTest(written=written):
                self.assertEqual(sizes_in(f"jacket, {written}"), {"letter": {"xl"}})

    def test_the_word_size_reads_one_without_a_clause_of_its_own(self):
        """Saying "size" is the other way a title makes a size unambiguous."""
        self.assertEqual(sizes_in("jacket size xl in black"), {"letter": {"xl"}})

    def test_a_single_letter_counts_only_where_a_title_puts_a_size(self):
        self.assertEqual(sizes_in("washed duck jacket, size m"), {"letter": {"m"}})
        # "M" is an initial and a model suffix far more often than a size.
        self.assertEqual(sizes_in("bosch gll 30 m laser level"), {})

    def test_a_bare_number_is_not_a_size_without_the_word(self):
        self.assertEqual(sizes_in("9 piece socket set, 3/8 inch drive"), {})
        self.assertEqual(sizes_in("running shoe size 9"), {"number": {"9"}})

    def test_a_half_size_survives_both_spellings(self):
        self.assertEqual(sizes_in("running shoe size 9.5"), {"number": {"9.5"}})
        self.assertEqual(sizes_in("cargo shorts, size 9 1/2"), {"number": {"9.5"}})

    def test_a_waist_by_inseam_is_read_without_the_word_size(self):
        self.assertEqual(sizes_in("levis 501 original fit jeans 32x30"), {"waist": {"32x30"}})

    def test_a_letter_size_is_not_mistaken_for_a_waist_measurement(self):
        # "xl" contains an x; a chart picked by looking for one gets this wrong.
        self.assertEqual(sizes_in("adidas unisex hoodie - xl"), {"letter": {"xl"}})

    def test_a_title_with_no_size_says_nothing_rather_than_guessing(self):
        self.assertEqual(sizes_in("dewalt 20v max cordless drill"), {})


class ReadingStylesTests(unittest.TestCase):
    def test_possessive_and_plain_spellings_are_one_answer(self):
        for written in ("men's", "mens", "men"):
            with self.subTest(written=written):
                self.assertEqual(styles_in(f"{written} rain jacket"), {"men"})

    def test_an_audience_the_title_never_names_is_not_invented(self):
        self.assertEqual(styles_in("packable rain jacket"), frozenset())


class FittingTests(unittest.TestCase):
    def setUp(self):
        self.shopper = wears(sizes=("xl", "12"), styles=("men", "unisex"))

    def test_the_wrong_size_in_the_right_department_is_refused(self):
        self.assertFalse(fits("mens hooded riding shirt - large", self.shopper))

    def test_the_right_size_passes(self):
        self.assertTrue(fits("mens hooded riding shirt - x-large", self.shopper))

    def test_the_right_size_in_the_wrong_department_is_still_refused(self):
        self.assertFalse(fits("womens fleece - x-large", self.shopper))

    def test_a_title_that_states_no_size_is_never_refused_for_it(self):
        """Silence is not a mismatch.

        Warehouse titles routinely omit the size, and refusing those would
        hide more real finds than the check saves.
        """
        self.assertTrue(fits("patagonia mens down sweater", self.shopper))

    def test_a_chart_this_person_never_filled_in_cannot_refuse_them(self):
        """A shirt size says nothing about a waist.

        This shopper declared letter and shoe sizes only. A waist-by-inseam
        number is a measurement they have no answer for, and having no answer
        is not the same as having the wrong one.
        """
        self.assertTrue(fits("levis 501 original fit jeans 32x30", self.shopper))

    def test_a_chart_this_person_did_fill_in_is_enforced(self):
        waisted = wears(sizes=("34x32",), styles=("men",))
        self.assertFalse(fits("levis 501 original fit jeans 32x30", waisted))
        self.assertTrue(fits("levis 501 original fit jeans 34x32", waisted))

    def test_declaring_no_styles_accepts_every_department(self):
        anyone = wears(sizes=("m",))
        self.assertTrue(fits("womens fleece - medium", anyone))
        self.assertTrue(fits("mens fleece - medium", anyone))

    def test_declaring_no_sizes_accepts_every_size(self):
        anyone = wears(styles=("men",))
        self.assertTrue(fits("mens fleece - xxxl", anyone))
        self.assertFalse(fits("womens fleece - xxxl", anyone))

    def test_a_childrens_department_is_refused_to_an_adult(self):
        self.assertFalse(fits("under armour boys tech tee - x-large", self.shopper))

    def test_a_shoe_size_is_checked_against_shoe_sizes_only(self):
        self.assertTrue(fits("mens running shoe size 12", self.shopper))
        self.assertFalse(fits("mens running shoe size 9.5", self.shopper))


class AdjectivesAreNotSizesTests(unittest.TestCase):
    """The words a size uses are the words a product description uses."""

    def test_a_size_word_describing_the_product_is_not_a_size(self):
        for title in (
            "purexa large floor squeegee for concrete floor",
            "bitlyle extra large inflatable swimming pool",
            "teruisi commercial hot water boiler, 50l/h large hot boiler",
            "meowant self-cleaning cat litter box, 106l large capacity",
        ):
            with self.subTest(title=title):
                self.assertEqual(sizes_in(title), {})

    def test_a_size_word_in_a_clause_of_its_own_is_a_size(self):
        self.assertEqual(
            sizes_in("mens hooded riding shirt w/ ce armor - large"),
            {"letter": {"l"}},
        )
        self.assertEqual(
            sizes_in("womens linen shorts - 2 pack, x-large, black"),
            {"letter": {"xl"}},
        )

    def test_a_measurement_carrying_a_unit_is_not_a_waist(self):
        for title in (
            'elkay dayton 33 x 21 inch double bowl drop-in sink',
            "kybolt 10x10 ft hardtop gazebo with heavy duty double roof",
        ):
            with self.subTest(title=title):
                self.assertEqual(sizes_in(title), {})

    def test_a_bare_pair_of_numbers_is_still_a_waist(self):
        self.assertEqual(sizes_in("levis 501 jeans 32x30"), {"waist": {"32x30"}})


class CanonicalSizeTests(unittest.TestCase):
    def test_a_word_that_is_not_a_size_reads_as_nothing(self):
        """So a stray match on "size chart" is discarded rather than believed."""
        self.assertEqual(canonical_size("chart"), "")

    def test_written_sizes_settle_on_one_spelling(self):
        self.assertEqual(canonical_size("Medium"), "m")
        self.assertEqual(canonical_size("9 1/2"), "9.5")
        self.assertEqual(canonical_size("32 x 30"), "32x30")


if __name__ == "__main__":
    unittest.main()
