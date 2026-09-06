"""Asking the provider's search for lots, politely and only once per term."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError

from auction_lens.acquisition import discover_searches
from auction_lens.acquisition.polling import PollLedger
from auction_lens.config import AcquisitionConfig, AcquisitionMode, ProviderConfig
from auction_lens.ingest import read_search_page
from auction_lens.models import Listing
from support import ROOT, FakeResponse, temporary_directory

SEARCH_PAGE = ROOT / "fixtures" / "nellis" / "search-page.html"
PAGE_URL = "https://example.invalid/search?query=soundbar"
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
CONTACT_USER_AGENT = "AuctionLens test contact=test@example.invalid"


def _page() -> str:
    return SEARCH_PAGE.read_text(encoding="utf-8")


class SearchPageTests(unittest.TestCase):
    def test_one_page_describes_every_lot_it_lists(self):
        rows = read_search_page(_page(), source="nellis", page_url=PAGE_URL)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [row["listing_id"] for row in rows], ["900000101", "900000102"]
        )

    def test_a_lot_that_has_already_sold_is_left_out(self):
        # Nothing can be bid on any more, so reporting it would be noise.
        rows = read_search_page(_page(), source="nellis", page_url=PAGE_URL)
        self.assertNotIn("900000103", [row["listing_id"] for row in rows])

    def test_each_lot_is_matched_to_the_address_the_page_links_it_at(self):
        rows = read_search_page(_page(), source="nellis", page_url=PAGE_URL)
        self.assertEqual(
            rows[0]["url"], "https://example.invalid/p/Example-Sound-Bar/900000101"
        )

    def test_a_search_result_carries_the_whole_grade(self):
        rough = read_search_page(_page(), source="nellis", page_url=PAGE_URL)[1]
        listing = Listing.from_mapping(rough)
        self.assertEqual(listing.grade.rating, 2)
        self.assertEqual(
            listing.conditions,
            ("used", "untested", "minor damage", "missing parts unknown", "assembly required"),
        )

    def test_a_search_result_has_no_taxonomy_so_it_states_no_category(self):
        # Only a lot's own page carries the taxonomy; claiming one here would
        # silently give every discovered lot the same wrong category.
        rows = read_search_page(_page(), source="nellis", page_url=PAGE_URL)
        self.assertNotIn("category", rows[0])

    def test_a_page_that_is_not_a_search_says_so(self):
        product_page = (ROOT / "fixtures" / "nellis" / "product-page.html").read_text(
            encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "not a search page"):
            read_search_page(product_page, source="nellis", page_url=PAGE_URL)


class RecordingOpener:
    """An opener that answers every request and remembers what was asked for.

    Set ``raise_not_modified`` to answer the way a provider does when the
    cached copy is still current.
    """

    def __init__(self, body: bytes):
        self.body = body
        self.urls: list[str] = []
        self.headers: list[dict] = []
        self.response_headers: dict[str, str] = {}
        self.posted: list[bytes | None] = []
        self.raise_not_modified = False

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        self.headers.append(dict(request.headers))
        self.posted.append(request.data)
        if self.raise_not_modified:
            raise HTTPError(request.full_url, 304, "Not Modified", {}, None)
        return FakeResponse(self.body, headers=self.response_headers)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.provider = ProviderConfig(provider_id="nellis", enabled=True)
        self.opener = RecordingOpener(_page().encode("utf-8"))
        self.slept: list[float] = []

    def _config(self, directory, **overrides) -> AcquisitionConfig:
        settings = {
            "mode": AcquisitionMode.AUTHORIZED_HTTP,
            "url": "https://example.invalid/browse",
            "search_url_template": "https://example.invalid/search?query={query}",
            "search_cache_dir": str(directory / "searches"),
            "ledger_file": str(directory / "ledger.json"),
            "seconds_between_searches": Decimal("5"),
            "run_mode": "development",
            "development_minimum_interval_seconds": 0,
        }
        settings.update(overrides)
        return AcquisitionConfig(**settings)

    def _discover(self, directory, terms, **overrides):
        config = self._config(directory, **overrides)
        with self._environment(config):
            return discover_searches(
                self.provider,
                config,
                terms,
                now=NOW,
                opener=self.opener,
                sleeper=self.slept.append,
            )

    def _environment(self, config):
        """The fetcher refuses to run without an identifying contact address."""
        return patch.dict(
            "os.environ", {config.user_agent_env: CONTACT_USER_AGENT}, clear=False
        )

    def test_each_term_is_asked_for_once(self):
        with temporary_directory() as directory:
            captures = self._discover(directory, ["soundbar", "monitor"])
        self.assertEqual([capture.term for capture in captures], ["soundbar", "monitor"])
        self.assertEqual(len(self.opener.urls), 2)
        self.assertIn("query=soundbar", self.opener.urls[0])

    def test_a_term_written_twice_is_asked_for_once(self):
        with temporary_directory() as directory:
            captures = self._discover(directory, ["soundbar", " Soundbar ", "soundbar"])
        self.assertEqual(len(captures), 1)
        self.assertEqual(len(self.opener.urls), 1)

    def test_more_terms_than_the_run_allows_are_cut_to_the_cap(self):
        with temporary_directory() as directory:
            captures = self._discover(
                directory, ["one", "two", "three"], max_searches_per_run=2
            )
        self.assertEqual([capture.term for capture in captures], ["one", "two"])

    def test_the_whole_run_counts_as_one_attempt_however_many_searches(self):
        # The ledger answers "may this run happen", not "how many requests".
        with temporary_directory() as directory:
            self._discover(directory, ["one", "two", "three"])
            attempts = PollLedger.at(
                self._config(directory).ledger_file
            ).attempts()
        self.assertEqual(len(attempts), 1)

    def test_searches_inside_one_run_are_spaced_out(self):
        with temporary_directory() as directory:
            self._discover(directory, ["one", "two", "three"])
        # The first needs no wait; the rest do.
        self.assertEqual(len(self.slept), 2)
        self.assertTrue(all(0 < delay <= 5 for delay in self.slept))

    def test_the_request_identifies_its_operator(self):
        with temporary_directory() as directory:
            self._discover(directory, ["soundbar"])
        self.assertIn("@", self.opener.headers[0]["User-agent"])

    def test_a_second_run_offers_the_provider_a_chance_to_say_nothing_changed(self):
        self.opener.response_headers = {"ETag": "search-etag-1"}
        with temporary_directory() as directory:
            self._discover(directory, ["soundbar"])
            self._discover(directory, ["soundbar"])
        self.assertNotIn("If-none-match", self.opener.headers[0])
        self.assertEqual(self.opener.headers[1]["If-none-match"], "search-etag-1")

    def test_an_unchanged_page_is_reused_instead_of_downloaded_again(self):
        self.opener.response_headers = {"ETag": "search-etag-1"}
        with temporary_directory() as directory:
            (first,) = self._discover(directory, ["soundbar"])
            self.opener.raise_not_modified = True
            (second,) = self._discover(directory, ["soundbar"])

        self.assertFalse(first.reused_cache)
        self.assertTrue(second.reused_cache)
        self.assertEqual(first.path, second.path)

    def test_the_branch_is_chosen_before_anything_is_searched_for(self):
        # A provider that scopes its catalogue by session serves its default
        # city otherwise, which is the wrong city for everybody but one person.
        with temporary_directory() as directory:
            self._discover(
                directory,
                ["soundbar"],
                session_url="https://example.invalid/change-shopping-location",
                session_fields={"shoppingLocationId": "2"},
            )
        self.assertEqual(self.opener.urls[0], "https://example.invalid/change-shopping-location")
        self.assertEqual(self.opener.posted[0], b"shoppingLocationId=2")
        self.assertIn("query=soundbar", self.opener.urls[1])

    def test_choosing_a_branch_is_spaced_like_any_other_request(self):
        with temporary_directory() as directory:
            self._discover(
                directory,
                ["soundbar", "monitor"],
                session_url="https://example.invalid/change-shopping-location",
                session_fields={"shoppingLocationId": "2"},
            )
        # Three requests, so two waits: nothing is fired back to back.
        self.assertEqual(len(self.opener.urls), 3)
        self.assertEqual(len(self.slept), 2)

    def test_a_provider_that_needs_no_branch_chosen_is_only_searched(self):
        with temporary_directory() as directory:
            self._discover(directory, ["soundbar"])
        self.assertEqual(len(self.opener.urls), 1)
        self.assertIsNone(self.opener.posted[0])

    def test_a_branch_address_that_is_not_public_https_is_refused(self):
        with temporary_directory() as directory:
            with self.assertRaisesRegex(ValueError, "public HTTPS"):
                self._discover(
                    directory,
                    ["soundbar"],
                    session_url="http://example.invalid/change-shopping-location",
                )
        self.assertEqual(self.opener.urls, [])

    def test_a_disabled_provider_is_never_contacted(self):
        with temporary_directory() as directory:
            self.provider = replace(self.provider, enabled=False)
            with self.assertRaisesRegex(RuntimeError, "provider is disabled"):
                self._discover(directory, ["soundbar"])
        self.assertEqual(self.opener.urls, [])

    def test_a_manual_provider_is_never_contacted(self):
        with temporary_directory() as directory:
            with self.assertRaisesRegex(RuntimeError, "authorized_http"):
                self._discover(directory, ["soundbar"], mode=AcquisitionMode.MANUAL)
        self.assertEqual(self.opener.urls, [])

    def test_a_search_address_that_is_not_public_https_is_refused(self):
        with temporary_directory() as directory:
            with self.assertRaisesRegex(ValueError, "public HTTPS"):
                self._discover(
                    directory,
                    ["soundbar"],
                    search_url_template="http://example.invalid/search?query={query}",
                )
        self.assertEqual(self.opener.urls, [])

    def test_a_run_with_no_terms_says_what_to_configure(self):
        with temporary_directory() as directory:
            with self.assertRaisesRegex(RuntimeError, "configure searches or pass --search"):
                self._discover(directory, [])

    def test_a_provider_with_no_search_address_configured_says_so(self):
        with temporary_directory() as directory:
            with self.assertRaisesRegex(RuntimeError, "search_url_template"):
                self._discover(directory, ["soundbar"], search_url_template="")


if __name__ == "__main__":
    unittest.main()
