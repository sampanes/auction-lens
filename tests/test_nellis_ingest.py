"""Converting a saved Nellis product page to canonical listing data."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal

from auction_lens.ingest import load_listings
from auction_lens.nellis_ingest import (
    GRADE_AXIS_NAMES,
    canonicalize_nellis_product,
    load_nellis_product_page,
    write_canonical_listings,
)
from support import ROOT, temporary_directory

GRADE_SAMPLES = ROOT / "fixtures" / "nellis" / "product-grade-samples.json"
PRODUCT_PAGE = ROOT / "fixtures" / "nellis" / "product-page.html"
PRODUCT_URL = "https://www.nellisauction.com/p/example-sound-bar/product-002"


def recorded_products() -> list[dict]:
    return json.loads(GRADE_SAMPLES.read_text(encoding="utf-8"))["samples"]


class ProductPageTests(unittest.TestCase):
    def test_streamed_remix_product_data_becomes_a_canonical_row(self):
        row = load_nellis_product_page(PRODUCT_PAGE, page_url=PRODUCT_URL)

        self.assertEqual(row["source"], "nellis")
        self.assertEqual(row["listing_id"], "0000000002")
        self.assertEqual(row["title"], "Example Karaoke Sound Bar with Wireless Microphones")
        self.assertEqual(row["url"], PRODUCT_URL)
        self.assertEqual(row["current_bid"], 8)
        self.assertEqual(row["estimated_retail"], 94.0)
        self.assertEqual(row["bid_count"], 8)
        self.assertEqual(row["ends_at"], "2026-09-07T01:10:00.000Z")
        self.assertEqual(row["category"], "Speakers")
        self.assertEqual(len(row["photo_urls"]), 3)

    def test_provider_grade_axis_names_are_renamed_by_the_adapter(self):
        row = load_nellis_product_page(PRODUCT_PAGE, page_url=PRODUCT_URL)
        self.assertEqual(
            row["grade"],
            {
                "condition": "Used",
                "functional": "Untested",
                "damage": "Minor",
                "missing_parts": "Unknown",
                "assembly": "Yes",
                "package": "Yes",
            },
        )
        self.assertEqual(row["quality_rating"], 2)

    def test_the_canonical_row_passes_through_the_regular_ingest_boundary(self):
        with temporary_directory() as directory:
            output = directory / "listings.json"
            row = load_nellis_product_page(PRODUCT_PAGE, page_url=PRODUCT_URL)
            write_canonical_listings([row], output)
            (listing,) = load_listings(output)

        self.assertEqual(listing.current_bid, Decimal("8"))
        self.assertEqual(
            listing.conditions,
            ("used", "untested", "minor damage", "missing parts unknown", "assembly required"),
        )
        self.assertEqual(listing.grade.rating, 2)
        self.assertTrue(listing.condition_photo_url.endswith("sample-002-3.jpg"))

    def test_a_page_without_product_data_has_an_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "Nellis product data was not found"):
            load_nellis_product_page(
                ROOT / "fixtures" / "nellis" / "browse-shell.html",
                page_url="https://www.nellisauction.com/browse",
            )


class RecordedProductTests(unittest.TestCase):
    def test_every_observed_grade_axis_has_a_canonical_name(self):
        for product in recorded_products():
            provider_axes = set(product["grade"]) - {"rating"}
            self.assertEqual(provider_axes - set(GRADE_AXIS_NAMES), set())

    def test_every_recorded_product_becomes_a_valid_listing(self):
        for product in recorded_products():
            with self.subTest(product=product["id"]):
                row = canonicalize_nellis_product(product, page_url=PRODUCT_URL)
                with temporary_directory() as directory:
                    output = directory / "listings.json"
                    write_canonical_listings([row], output)
                    (listing,) = load_listings(output)
                self.assertEqual(listing.source, "nellis")


if __name__ == "__main__":
    unittest.main()
