"""Reading a provider's condition grades, including the two traps in them."""

from __future__ import annotations

import json
import unittest

from auction_lens.grading import AXES, Grade, Tag, read_grade
from auction_lens.ingest import canonical_grade
from auction_lens.models import Listing
from support import ROOT

GRADE_SAMPLES = ROOT / "fixtures" / "nellis" / "product-grade-samples.json"


def _samples() -> list[dict]:
    return json.loads(GRADE_SAMPLES.read_text(encoding="utf-8"))["samples"]


def _canonical_grade(sample: dict) -> dict[str, str]:
    """Rename a recorded sample's axes the same way the ingest adapter does."""
    return canonical_grade(sample["grade"])


class PolarityTests(unittest.TestCase):
    """The trap: the same word means opposite things on different axes."""

    def test_yes_is_good_news_about_packaging_and_bad_news_about_assembly(self):
        grade = read_grade({"package": "Yes", "assembly": "Yes"})
        colours = {tag.axis: tag.tag for tag in grade.tags}
        self.assertEqual(colours["package"], Tag.GREEN)
        self.assertEqual(colours["assembly"], Tag.RED)

    def test_no_is_bad_news_about_packaging_and_good_news_about_assembly(self):
        grade = read_grade({"package": "No", "assembly": "No"})
        colours = {tag.axis: tag.tag for tag in grade.tags}
        self.assertEqual(colours["package"], Tag.RED)
        self.assertEqual(colours["assembly"], Tag.GREEN)

    def test_every_axis_answers_its_own_question_in_its_own_words(self):
        grade = read_grade({"assembly": "Yes", "missing_parts": "Yes"})
        self.assertEqual(
            [tag.label for tag in grade.tags],
            ["Missing Parts", "Assembly Required"],
        )


class UnansweredAxisTests(unittest.TestCase):
    """The other trap: the provider shows nothing where it has no answer."""

    def test_an_unanswered_axis_is_amber_and_says_which_question_it_is(self):
        grade = read_grade({"missing_parts": "Unknown"})
        (tag,) = grade.tags
        self.assertEqual(tag.tag, Tag.AMBER)
        self.assertEqual(tag.label, "Missing Parts Unknown")

    def test_an_unanswered_axis_still_counts_as_a_concern(self):
        grade = read_grade({"missing_parts": "Unknown", "damage": "None"})
        self.assertEqual([tag.label for tag in grade.concerns], ["Missing Parts Unknown"])

    def test_an_axis_this_provider_does_not_grade_is_absent_not_amber(self):
        grade = read_grade({"condition": "New"})
        self.assertEqual([tag.axis for tag in grade.tags], ["condition"])

    def test_a_word_nobody_has_seen_before_is_amber_rather_than_ignored(self):
        grade = read_grade({"condition": "Refurbished By Elves"})
        self.assertEqual(grade.tags[0].tag, Tag.AMBER)


class RatingTests(unittest.TestCase):
    def test_a_rating_outside_the_scale_is_refused(self):
        with self.assertRaisesRegex(ValueError, "rating must be between 1 and 5"):
            Grade(rating=9)

    def test_a_provider_that_does_not_rate_its_lots_leaves_it_unset(self):
        self.assertIsNone(read_grade({"condition": "New"}).rating)


class RecordedSampleTests(unittest.TestCase):
    """The vocabulary recorded from real listings still reads the way it did."""

    def test_every_recorded_sample_maps_to_the_colours_that_were_observed(self):
        expected = {
            "sample-001": ["green"] * 6,
            "sample-002": ["red", "red", "red", "amber", "red", "green"],
            "sample-003": ["red", "green", "green", "green", "green", "green"],
        }
        for sample in _samples():
            with self.subTest(sample=sample["id"]):
                grade = read_grade(_canonical_grade(sample), sample["grade"]["rating"])
                colours = [str(tag.tag) for tag in grade.tags]
                self.assertEqual(colours, expected[sample["id"]])

    def test_the_used_but_otherwise_clean_lot_still_carries_the_top_rating(self):
        # The provider's rating is its own opinion, not a summary of the tags.
        sample = next(s for s in _samples() if s["id"] == "sample-003")
        grade = read_grade(_canonical_grade(sample), sample["grade"]["rating"])
        self.assertEqual(grade.rating, 5)
        self.assertEqual([tag.label for tag in grade.concerns], ["Used"])

    def test_every_axis_the_samples_use_is_one_this_project_knows(self):
        known = {axis.name for axis in AXES}
        for sample in _samples():
            self.assertEqual(set(_canonical_grade(sample)) - known, set())


class GradedListingTests(unittest.TestCase):
    def test_a_grade_becomes_the_condition_words_scoring_matches_on(self):
        listing = Listing.from_mapping(
            {
                "source": "nellis",
                "listing_id": "1",
                "title": "Example Karaoke Sound Bar",
                "url": "https://example.invalid/p/1",
                "current_bid": "8.00",
                "conditions": "ignored because the grade is authoritative",
                "grade": {"condition": "Used", "damage": "Minor", "package": "Yes"},
                "quality_rating": 2,
            }
        )
        self.assertEqual(listing.conditions, ("used", "minor damage"))
        self.assertEqual(listing.grade.rating, 2)

    def test_the_gallery_separates_the_stock_photo_from_the_real_one(self):
        listing = Listing.from_mapping(
            {
                "source": "nellis",
                "listing_id": "1",
                "title": "Example Sound Bar",
                "url": "https://example.invalid/p/1",
                "current_bid": "8.00",
                "photo_urls": [
                    "https://example.invalid/stock.jpg",
                    "https://example.invalid/warehouse.jpg",
                ],
            }
        )
        self.assertEqual(listing.stock_photo_url, "https://example.invalid/stock.jpg")
        self.assertEqual(
            listing.condition_photo_url, "https://example.invalid/warehouse.jpg"
        )

    def test_a_provider_that_sends_one_image_still_reads_as_a_gallery_of_one(self):
        listing = Listing.from_mapping(
            {
                "source": "nellis",
                "listing_id": "1",
                "title": "Example Sound Bar",
                "url": "https://example.invalid/p/1",
                "current_bid": "8.00",
                "image_url": "https://example.invalid/only.jpg",
            }
        )
        self.assertEqual(listing.photo_urls, ("https://example.invalid/only.jpg",))
        self.assertEqual(listing.condition_photo_url, "https://example.invalid/only.jpg")


if __name__ == "__main__":
    unittest.main()
