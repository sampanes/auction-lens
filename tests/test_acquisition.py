"""Fetching one authorized page, and the limits that govern it."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from auction_lens.acquisition import fetch_authorized_page
from support import (
    NELLIS_BROWSE_FIXTURE,
    FakeResponse,
    RecordingOpener,
    example_config,
    temporary_directory,
)

CONTACT_USER_AGENT = "AuctionLens test contact=test@example.invalid"


class AuthorizedFetchTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()

    def test_request_identifies_the_operator_and_caches_the_body(self):
        opener = RecordingOpener(
            FakeResponse(b"<html>fixture</html>", headers={"ETag": '"fixture-v1"'})
        )
        with temporary_directory() as directory:
            acquisition = self._acquisition(directory)
            with self._environment(acquisition):
                result = fetch_authorized_page(
                    self.config.provider, acquisition, now=self._instant(), opener=opener
                )
            cached = result.cache_path.read_bytes()
            metadata = result.cache_path.with_suffix(".html.metadata.json").read_text("utf-8")

        self.assertEqual(result.status, 200)
        self.assertEqual(cached, b"<html>fixture</html>")
        self.assertIn("test@example.invalid", opener.calls[0][0].get_header("User-agent"))
        self.assertIn("fixture-v1", metadata)

    def test_second_request_inside_the_interval_is_refused(self):
        opener = RecordingOpener(FakeResponse(b"<html>fixture</html>"))
        instant = self._instant()
        with temporary_directory() as directory:
            acquisition = self._acquisition(directory)
            with self._environment(acquisition):
                fetch_authorized_page(
                    self.config.provider, acquisition, now=instant, opener=opener
                )
                with self.assertRaisesRegex(RuntimeError, "minimum interval"):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=instant + timedelta(minutes=5),
                        opener=opener,
                    )
        self.assertEqual(opener.request_count, 1)

    def test_daily_limit_counts_the_provider_local_day(self):
        opener = RecordingOpener(FakeResponse(b"<html>fixture</html>"))
        instant = self._instant()
        with temporary_directory() as directory:
            acquisition = replace(
                self._acquisition(directory),
                max_requests_per_day=1,
                minimum_interval_minutes=1,
            )
            with self._environment(acquisition):
                fetch_authorized_page(
                    self.config.provider, acquisition, now=instant, opener=opener
                )
                with self.assertRaisesRegex(RuntimeError, "daily request limit"):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=instant + timedelta(hours=2),
                        opener=opener,
                    )
        self.assertEqual(opener.request_count, 1)

    def test_development_mode_allows_repeated_but_throttled_requests(self):
        opener = RecordingOpener(FakeResponse(b"development fixture"))
        instant = datetime(2026, 9, 5, 16, tzinfo=timezone.utc)
        with temporary_directory() as directory:
            acquisition = replace(
                self._acquisition(directory),
                run_mode="development",
                development_minimum_interval_seconds=2,
            )
            with self._environment(acquisition):
                fetch_authorized_page(
                    self.config.provider, acquisition, now=instant, opener=opener
                )
                with self.assertRaisesRegex(RuntimeError, "development minimum interval"):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=instant + timedelta(seconds=1),
                        opener=opener,
                    )
                for offset in (2, 4):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=instant + timedelta(seconds=offset),
                        opener=opener,
                    )
        self.assertEqual(opener.request_count, 3)

    def test_a_disabled_provider_is_never_contacted(self):
        opener = RecordingOpener(FakeResponse(b""))
        with temporary_directory() as directory:
            acquisition = self._acquisition(directory)
            provider = replace(self.config.provider, enabled=False)
            with self.assertRaisesRegex(RuntimeError, "provider is disabled"):
                fetch_authorized_page(provider, acquisition, now=self._instant(), opener=opener)
        self.assertEqual(opener.request_count, 0)

    def test_a_non_https_url_is_refused(self):
        opener = RecordingOpener(FakeResponse(b""))
        with temporary_directory() as directory:
            acquisition = replace(
                self._acquisition(directory), url="http://example.invalid/public-listings"
            )
            with self._environment(acquisition):
                with self.assertRaisesRegex(ValueError, "public HTTPS"):
                    fetch_authorized_page(
                        self.config.provider, acquisition, now=self._instant(), opener=opener
                    )
        self.assertEqual(opener.request_count, 0)

    def test_a_user_agent_without_a_contact_address_is_refused(self):
        opener = RecordingOpener(FakeResponse(b""))
        with temporary_directory() as directory:
            acquisition = self._acquisition(directory)
            with patch.dict("os.environ", {acquisition.user_agent_env: "AuctionLens"}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "authorized contact email"):
                    fetch_authorized_page(
                        self.config.provider, acquisition, now=self._instant(), opener=opener
                    )
        self.assertEqual(opener.request_count, 0)

    def _acquisition(self, directory):
        return replace(
            self.config.acquisition,
            mode="authorized_http",
            url="https://example.invalid/public-listings",
            timezone="America/Phoenix",
            max_requests_per_day=2,
            minimum_interval_minutes=180,
            cache_file=str(directory / "response.html"),
            ledger_file=str(directory / "ledger.json"),
        )

    def _environment(self, acquisition):
        return patch.dict(
            "os.environ", {acquisition.user_agent_env: CONTACT_USER_AGENT}, clear=False
        )

    def _instant(self):
        return datetime(2026, 9, 4, 16, tzinfo=timezone.utc)


class BrowseFixtureTests(unittest.TestCase):
    """The committed fixture must stay redacted and structurally useful."""

    def test_redacted_fixture_preserves_acquisition_boundaries(self):
        fixture = NELLIS_BROWSE_FIXTURE.read_text(encoding="utf-8")
        self.assertIn('action="/search"', fixture)
        self.assertIn('href="/browse/az"', fixture)
        self.assertIn("window.__remixContext", fixture)
        self.assertIn('"APP_PUBLIC_ALGOLIA_API_KEY": "REDACTED"', fixture)
        self.assertNotIn("@", fixture)


if __name__ == "__main__":
    unittest.main()
