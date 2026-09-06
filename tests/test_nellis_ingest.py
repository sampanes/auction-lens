"""Reading a saved provider page into a canonical listing row."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from auction_lens.ingest import read_product_page
from auction_lens.ingest.turbo_stream import decode
from auction_lens.models import Listing
from support import ROOT

PRODUCT_PAGE = ROOT / "fixtures" / "nellis" / "product-page.html"


def _payload(values: list) -> str:
    """One streamed payload, written the way the provider writes it."""
    return json.dumps(values, separators=(",", ":")) + "\n"


def _page() -> str:
    return PRODUCT_PAGE.read_text(encoding="utf-8")


class TurboStreamTests(unittest.TestCase):
    """The envelope: a flat array of values, and indexes describing the graph."""

    def test_an_object_reads_its_keys_and_values_from_the_same_array(self):
        # Index 1 is the key "colour"; index 2 is its value.
        self.assertEqual(decode(_payload([{"_1": 2}, "colour", "green"])), {"colour": "green"})

    def test_a_value_used_twice_is_written_once_and_pointed_at_twice(self):
        payload = _payload([{"_1": 3, "_2": 3}, "near", "far", "same"])
        self.assertEqual(decode(payload), {"near": "same", "far": "same"})

    def test_an_array_is_a_list_of_indexes(self):
        self.assertEqual(decode(_payload([[1, 2], "first", "second"])), ["first", "second"])

    def test_the_null_marker_reads_as_nothing(self):
        self.assertEqual(decode(_payload([{"_1": -5}, "notes"])), {"notes": None})

    def test_a_marker_nobody_has_decoded_is_refused_rather_than_guessed_at(self):
        with self.assertRaisesRegex(ValueError, "unknown marker -2"):
            decode(_payload([{"_1": -2}, "notes"]))

    def test_a_payload_that_points_at_itself_is_refused(self):
        with self.assertRaisesRegex(ValueError, "refers to itself"):
            decode(_payload([{"_1": 0}, "self"]))

    def test_a_payload_that_points_past_its_own_end_is_refused(self):
        with self.assertRaisesRegex(ValueError, "missing index 9"):
            decode(_payload([{"_1": 9}, "absent"]))

    def test_an_empty_payload_is_refused(self):
        with self.assertRaisesRegex(ValueError, "non-empty array"):
            decode(_payload([]))


class ProductPageTests(unittest.TestCase):
    def test_a_saved_page_becomes_a_canonical_row(self):
        row = read_product_page(_page(), source="nellis")
        self.assertEqual(row["source"], "nellis")
        self.assertEqual(row["listing_id"], "900000001")
        self.assertEqual(row["current_bid"], "8")
        self.assertEqual(row["estimated_retail"], "94.0")
        self.assertEqual(row["bid_count"], 8)
        self.assertEqual(row["location"], "Example Warehouse")

    def test_both_ids_are_carried_because_they_answer_different_questions(self):
        row = read_product_page(_page(), source="nellis")
        # id names this auction and builds the URL; inventoryNumber names the
        # physical item and survives it being relisted.
        self.assertEqual(row["listing_id"], "900000001")
        self.assertEqual(row["inventory_id"], "0000000002")

    def test_the_page_states_its_own_address_rather_than_it_being_rebuilt(self):
        row = read_product_page(_page(), source="nellis")
        self.assertEqual(
            row["url"], "https://example.invalid/p/Example-Karaoke-Sound-Bar/900000001"
        )

    def test_the_narrower_taxonomy_wins_because_interests_match_on_it(self):
        self.assertEqual(read_product_page(_page(), source="nellis")["category"], "Speakers")

    def test_the_provider_axis_names_are_renamed_to_the_canonical_ones(self):
        grade = read_product_page(_page(), source="nellis")["grade"]
        self.assertEqual(
            grade,
            {
                "condition": "Used",
                "functional": "Untested",
                "damage": "Minor",
                "missing_parts": "Unknown",
                "assembly": "Yes",
                "package": "Yes",
            },
        )

    def test_the_gallery_takes_the_address_that_fetches_not_the_storage_path(self):
        # fullPath is relative for the provider's own photographs, so reading it
        # would put an unopenable string in a report.
        urls = read_product_page(_page(), source="nellis")["photo_urls"]
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(url.startswith("https://") for url in urls))
        self.assertIn("shelf", urls[-1])

    def test_a_page_with_no_streamed_payload_says_what_is_wrong(self):
        with self.assertRaisesRegex(ValueError, "not a product page"):
            read_product_page("<html><body>Nothing here</body></html>", source="nellis")

    def test_a_page_whose_payload_holds_no_product_says_so(self):
        page = _page().replace("routes/p.$title.$productId._index", "routes/something.else")
        with self.assertRaisesRegex(ValueError, "no product"):
            read_product_page(page, source="nellis")


class PulledListingTests(unittest.TestCase):
    """A pulled lot has to be indistinguishable from a hand-written one."""

    def test_a_pulled_page_builds_a_listing_with_its_grade_intact(self):
        listing = Listing.from_mapping(read_product_page(_page(), source="nellis"))
        self.assertEqual(listing.current_bid, Decimal("8"))
        self.assertEqual(listing.grade.rating, 2)
        self.assertEqual(
            listing.conditions,
            ("used", "untested", "minor damage", "missing parts unknown", "assembly required"),
        )

    def test_the_unanswered_axis_survives_all_the_way_into_the_listing(self):
        listing = Listing.from_mapping(read_product_page(_page(), source="nellis"))
        amber = [tag.label for tag in listing.grade.concerns if tag.tag == "amber"]
        self.assertEqual(amber, ["Missing Parts Unknown"])

    def test_the_last_photo_is_the_one_taken_of_this_actual_lot(self):
        listing = Listing.from_mapping(read_product_page(_page(), source="nellis"))
        self.assertIn("stock", listing.stock_photo_url)
        self.assertIn("shelf", listing.condition_photo_url)


if __name__ == "__main__":
    unittest.main()
