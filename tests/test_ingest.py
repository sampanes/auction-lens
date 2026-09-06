"""Reading canonical listing files into the domain model."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from auction_lens.ingest import load_listings
from support import SYNTHETIC_LISTINGS, temporary_directory

MINIMAL_LISTING = {
    "source": "nellis",
    "listing_id": "row-1",
    "title": "Example lot",
    "url": "https://example.invalid/auction/row-1",
    "current_bid": "5.00",
}


class JsonIngestTests(unittest.TestCase):
    def test_wrapped_listings_are_read_with_exact_money(self):
        listings = load_listings(SYNTHETIC_LISTINGS)
        self.assertEqual(len(listings), 2)
        self.assertEqual(listings[0].current_bid, Decimal("18.00"))

    def test_a_graded_lot_gets_its_condition_words_from_the_grade(self):
        # The provider left missing_parts unanswered, which is its own concern
        # rather than a silence; see docs/DATA_ACQUISITION.md.
        listings = load_listings(SYNTHETIC_LISTINGS)
        self.assertEqual(listings[0].conditions, ("used", "missing parts unknown"))
        self.assertEqual(listings[0].grade.rating, 3)

    def test_the_gallery_keeps_the_order_the_provider_sent(self):
        (soundbar, _) = load_listings(SYNTHETIC_LISTINGS)
        self.assertTrue(soundbar.stock_photo_url.endswith("stock.jpg"))
        self.assertTrue(soundbar.condition_photo_url.endswith("shelf.jpg"))

    def test_a_bare_list_is_also_accepted(self):
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([MINIMAL_LISTING]), encoding="utf-8")
            self.assertEqual(load_listings(path)[0].listing_id, "row-1")

    def test_a_file_saved_with_a_byte_order_mark_still_loads(self):
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([MINIMAL_LISTING]), encoding="utf-8-sig")
            self.assertEqual(load_listings(path)[0].listing_id, "row-1")

    def test_an_object_without_a_listings_list_is_explained(self):
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps({"items": [MINIMAL_LISTING]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "'listings' list"):
                load_listings(path)

    def test_an_unreadable_timestamp_names_the_field(self):
        broken = {**MINIMAL_LISTING, "ends_at": "next Tuesday"}
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([broken]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ends_at must be an ISO-8601 timestamp"):
                load_listings(path)

    def test_a_missing_required_field_names_it(self):
        incomplete = {key: value for key, value in MINIMAL_LISTING.items() if key != "url"}
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([incomplete]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing required listing fields: url"):
                load_listings(path)

    def test_a_bad_row_names_its_file_and_position(self):
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([MINIMAL_LISTING, 42]), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, r"listings\.json: listing 2 must be an object"
            ):
                load_listings(path)

    def test_duplicate_listing_keys_are_rejected(self):
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(
                json.dumps([MINIMAL_LISTING, MINIMAL_LISTING]), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ValueError, "listing 2 duplicates nellis/row-1 from listing 1"
            ):
                load_listings(path)

    def test_fractional_bid_count_is_not_silently_truncated(self):
        listing = {**MINIMAL_LISTING, "bid_count": 1.5}
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([listing]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bid_count must be a whole number"):
                load_listings(path)

    def test_non_finite_money_is_rejected_plainly(self):
        listing = {**MINIMAL_LISTING, "current_bid": "Infinity"}
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([listing]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "current_bid must be a finite number"):
                load_listings(path)

    def test_whitespace_does_not_satisfy_a_required_field(self):
        listing = {**MINIMAL_LISTING, "title": "   "}
        with temporary_directory() as directory:
            path = directory / "listings.json"
            path.write_text(json.dumps([listing]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing required listing fields: title"):
                load_listings(path)


class CsvIngestTests(unittest.TestCase):
    def test_delimited_conditions_and_dimensions_are_parsed(self):
        header = "source,listing_id,title,url,current_bid,conditions,package_dimensions_in"
        row = (
            "nellis,row-2,Big lot,https://example.invalid/auction/row-2,"
            "7.00,Used|Untested,70x31x45"
        )
        with temporary_directory() as directory:
            path = directory / "listings.csv"
            path.write_text(f"{header}\n{row}\n", encoding="utf-8")
            listing = load_listings(path)[0]
        self.assertEqual(listing.conditions, ("untested", "used"))
        self.assertEqual(
            [str(value) for value in listing.package_dimensions_in], ["70", "31", "45"]
        )


class UnsupportedInputTests(unittest.TestCase):
    def test_another_extension_is_refused(self):
        with temporary_directory() as directory:
            path = directory / "listings.xlsx"
            path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, ".json or .csv"):
                load_listings(path)


if __name__ == "__main__":
    unittest.main()
