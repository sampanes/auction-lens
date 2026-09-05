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
        self.assertEqual(listings[0].conditions, ("used",))

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
