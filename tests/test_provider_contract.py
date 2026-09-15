"""The complete read-only provider conversation, observed at its public boundary."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError

from auction_lens.acquisition import discover_searches
from auction_lens.config import AcquisitionConfig, ProviderConfig
from support import FakeResponse, temporary_directory

CONTACT_USER_AGENT = "AuctionLens test contact=operator@auction-lens.dev"
FIRST_RUN = datetime(2026, 9, 6, 12, tzinfo=UTC)


class TranscriptOpener:
    """Record the protocol facts while acting like a cache-aware provider."""

    def __init__(self):
        self.calls: list[dict] = []
        self.not_modified = False

    def __call__(self, request, timeout):
        method = request.get_method()
        self.calls.append(
            {
                "method": method,
                "url": request.full_url,
                "headers": {
                    name.casefold(): value for name, value in request.header_items()
                },
                "body": request.data,
                "timeout": timeout,
            }
        )
        if self.not_modified and method == "GET":
            raise HTTPError(request.full_url, 304, "Not Modified", {}, None)
        return FakeResponse(
            b"<html>synthetic search page</html>",
            headers={
                "ETag": '"catalogue-1"',
                "Last-Modified": "Sun, 06 Sep 2026 12:00:00 GMT",
            },
        )


class AuthorizedProviderProtocolTests(unittest.TestCase):
    def test_a_cached_session_scoped_search_has_one_explicit_polite_transcript(self):
        opener = TranscriptOpener()
        slept: list[float] = []

        with temporary_directory() as directory:
            config = AcquisitionConfig(
                mode="authorized_http",
                authorization_confirmed=True,
                url="https://example.invalid/browse",
                search_url_template="https://example.invalid/search?query={query}",
                search_cache_dir=str(directory / "searches"),
                ledger_file=str(directory / "polls.json"),
                run_mode="development",
                development_minimum_interval_seconds=0,
                timeout_seconds=17,
                seconds_between_searches=Decimal("2"),
                session_url="https://example.invalid/change-shopping-location",
                session_fields={
                    "shoppingLocationId": "2",
                    "returnUrl": "/browse",
                },
                session_change_authorized=True,
            )
            provider = ProviderConfig(provider_id="synthetic", enabled=True)
            environment = {config.user_agent_env: CONTACT_USER_AGENT}

            with patch.dict("os.environ", environment, clear=False):
                discover_searches(
                    provider,
                    config,
                    ("soundbar", "power tools"),
                    now=FIRST_RUN,
                    opener=opener,
                    sleeper=slept.append,
                )
                opener.calls.clear()
                slept.clear()
                opener.not_modified = True
                captures = discover_searches(
                    provider,
                    config,
                    ("soundbar", "power tools"),
                    now=FIRST_RUN + timedelta(hours=1),
                    opener=opener,
                    sleeper=slept.append,
                )

        self.assertEqual(
            [
                (call["method"], call["url"], call["body"], call["timeout"])
                for call in opener.calls
            ],
            [
                (
                    "POST",
                    "https://example.invalid/change-shopping-location",
                    b"shoppingLocationId=2&returnUrl=%2Fbrowse",
                    17,
                ),
                (
                    "GET",
                    "https://example.invalid/search?query=soundbar",
                    None,
                    17,
                ),
                (
                    "GET",
                    "https://example.invalid/search?query=power+tools",
                    None,
                    17,
                ),
            ],
        )
        for call in opener.calls:
            self.assertEqual(call["headers"]["user-agent"], CONTACT_USER_AGENT)
            self.assertEqual(
                call["headers"]["accept"], "text/html,application/xhtml+xml"
            )
        self.assertNotIn("if-none-match", opener.calls[0]["headers"])
        for call in opener.calls[1:]:
            self.assertEqual(call["headers"]["if-none-match"], '"catalogue-1"')
            self.assertEqual(
                call["headers"]["if-modified-since"],
                "Sun, 06 Sep 2026 12:00:00 GMT",
            )
        self.assertEqual(len(slept), 2)
        self.assertTrue(all(0 < delay <= 2 for delay in slept))
        self.assertTrue(all(capture.reused_cache for capture in captures))
        self.assertTrue(all(capture.fetched_at == FIRST_RUN for capture in captures))


if __name__ == "__main__":
    unittest.main()
