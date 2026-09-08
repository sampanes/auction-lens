"""Fetching one authorized page, and the limits that govern it."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.request import Request

from auction_lens.acquisition import fetch_authorized_page
from auction_lens.http_safety import PublicHttpsRedirectHandler
from support import (
    NELLIS_BROWSE_FIXTURE,
    FakeResponse,
    RecordingOpener,
    example_config,
    temporary_directory,
)

CONTACT_USER_AGENT = "AuctionLens test contact=operator@auction-lens.dev"


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
        self.assertIn("operator@auction-lens.dev", opener.calls[0][0].get_header("User-agent"))
        self.assertIn("fixture-v1", metadata)

    @patch("auction_lens.acquisition.fetch.public_https_opener")
    def test_the_default_fetch_path_uses_the_redirect_safe_opener(self, opener_factory):
        opener = RecordingOpener(FakeResponse(b"<html>fixture</html>"))
        opener_factory.return_value = opener
        with temporary_directory() as directory:
            acquisition = self._acquisition(directory)
            with self._environment(acquisition):
                fetch_authorized_page(
                    self.config.provider, acquisition, now=self._instant()
                )
        opener_factory.assert_called_once_with()
        self.assertEqual(opener.request_count, 1)

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
        instant = datetime(2026, 9, 5, 16, tzinfo=UTC)
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

    def test_authorized_http_requires_an_explicit_permission_confirmation(self):
        opener = RecordingOpener(FakeResponse(b""))
        with temporary_directory() as directory:
            acquisition = replace(
                self._acquisition(directory), authorization_confirmed=False
            )
            with self._environment(acquisition):
                with self.assertRaisesRegex(
                    RuntimeError, "authorization_confirmed = true"
                ):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=self._instant(),
                        opener=opener,
                    )
        self.assertEqual(opener.request_count, 0)

    def test_text_that_says_true_is_not_an_authorization_confirmation(self):
        opener = RecordingOpener(FakeResponse(b""))
        with temporary_directory() as directory:
            acquisition = replace(
                self._acquisition(directory), authorization_confirmed="true"
            )
            with self._environment(acquisition):
                with self.assertRaisesRegex(
                    RuntimeError, "authorization_confirmed = true"
                ):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=self._instant(),
                        opener=opener,
                    )
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
            agent = {acquisition.user_agent_env: "AuctionLens"}
            with patch.dict("os.environ", agent, clear=False):
                with self.assertRaisesRegex(RuntimeError, "operator's contact email"):
                    fetch_authorized_page(
                        self.config.provider, acquisition, now=self._instant(), opener=opener
                    )
        self.assertEqual(opener.request_count, 0)

    def test_non_public_targets_are_refused_before_any_request(self):
        opener = RecordingOpener(FakeResponse(b""))
        targets = (
            "https://localhost/listings",
            "https://auction-server/listings",
            "https://127.0.0.1/listings",
            "https://127.1/listings",
            "https://10.20.30.40/listings",
            "https://169.254.169.254/latest",
            "https://240.0.0.1/listings",
            "https://[::1]/listings",
        )
        with temporary_directory() as directory:
            for target in targets:
                with self.subTest(target=target):
                    acquisition = replace(self._acquisition(directory), url=target)
                    with self._environment(acquisition):
                        with self.assertRaisesRegex(ValueError, "public"):
                            fetch_authorized_page(
                                self.config.provider,
                                acquisition,
                                now=self._instant(),
                                opener=opener,
                            )
        self.assertEqual(opener.request_count, 0)

    def test_an_example_contact_address_is_refused(self):
        opener = RecordingOpener(FakeResponse(b""))
        with temporary_directory() as directory:
            acquisition = self._acquisition(directory)
            agent = {
                acquisition.user_agent_env: "AuctionLens/1.0 (contact: you@example.com)"
            }
            with patch.dict("os.environ", agent, clear=False):
                with self.assertRaisesRegex(RuntimeError, "example or placeholder"):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=self._instant(),
                        opener=opener,
                    )
        self.assertEqual(opener.request_count, 0)

    def test_a_subdomain_of_an_example_contact_address_is_refused(self):
        opener = RecordingOpener(FakeResponse(b""))
        with temporary_directory() as directory:
            acquisition = self._acquisition(directory)
            agent = {
                acquisition.user_agent_env: "AuctionLens contact=you@docs.example.com"
            }
            with patch.dict("os.environ", agent, clear=False):
                with self.assertRaisesRegex(RuntimeError, "example or placeholder"):
                    fetch_authorized_page(
                        self.config.provider,
                        acquisition,
                        now=self._instant(),
                        opener=opener,
                    )
        self.assertEqual(opener.request_count, 0)

    def _acquisition(self, directory):
        return replace(
            self.config.acquisition,
            mode="authorized_http",
            authorization_confirmed=True,
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
        return datetime(2026, 9, 4, 16, tzinfo=UTC)


class PublicRedirectTests(unittest.TestCase):
    def setUp(self):
        self.handler = PublicHttpsRedirectHandler()
        self.request = Request("https://provider.example/listings")

    def test_a_redirect_cannot_leave_the_authorized_host(self):
        with self.assertRaisesRegex(RuntimeError, "different origin.*reconfirm"):
            self._redirect("https://other.example/listings")

    def test_a_redirect_cannot_downgrade_from_https(self):
        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            self._redirect("http://provider.example/listings")

    def test_a_redirect_cannot_reach_a_private_address(self):
        with self.assertRaisesRegex(ValueError, "non-public IP"):
            self._redirect("https://127.0.0.1/listings")

    def test_a_redirect_cannot_switch_ports_on_the_same_host(self):
        with self.assertRaisesRegex(RuntimeError, "different origin.*reconfirm"):
            self._redirect("https://provider.example:444/listings")

    def test_an_unusually_spelled_address_is_stopped_by_the_origin_rule(self):
        # 0x7f.1 is a legacy spelling of a loopback address. Recognising the
        # spelling is not what protects the operator here: the redirect is not
        # the authorized origin, which is true of every address it could name.
        with self.assertRaisesRegex(RuntimeError, "different origin.*reconfirm"):
            self._redirect("https://0x7f.1/listings")

    def test_a_same_host_public_https_redirect_is_allowed(self):
        redirected = self._redirect("https://provider.example/current-listings")
        self.assertEqual(
            redirected.full_url, "https://provider.example/current-listings"
        )

    def _redirect(self, target):
        return self.handler.redirect_request(
            self.request,
            None,
            302,
            "Found",
            {},
            target,
        )


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
