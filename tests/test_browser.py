"""The browser transport answers exactly as the urllib opener it replaced."""

from __future__ import annotations

import unittest
from urllib.error import HTTPError
from urllib.request import Request

from auction_lens.providers.browser import _forwarded, answer

URL = "https://example.invalid/search?query=soundbar"


class AnswerTests(unittest.TestCase):
    def test_a_page_is_returned_with_headers_read_regardless_of_case(self):
        response = answer(URL, 200, "OK", {"etag": "v1"}, b"<html></html>")
        with response:
            self.assertEqual(response.read(), b"<html></html>")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("ETag"), "v1")

    def test_not_modified_raises_as_urllib_does_so_the_cache_is_kept(self):
        with self.assertRaises(HTTPError) as raised:
            answer(URL, 304, "Not Modified", {}, b"")
        self.assertEqual(raised.exception.code, 304)

    def test_a_refusal_carries_its_status_and_retry_advice(self):
        with self.assertRaises(HTTPError) as raised:
            answer(URL, 429, "Too Many Requests", {"retry-after": "60"}, b"")
        self.assertEqual(raised.exception.code, 429)
        self.assertEqual(raised.exception.headers.get("Retry-After"), "60")


class ForwardedHeaderTests(unittest.TestCase):
    def test_only_revalidation_headers_pass_and_the_session_names_itself(self):
        request = Request(
            URL,
            headers={
                "User-Agent": "someone else",
                "Accept": "text/html",
                "If-None-Match": "v1",
                "If-Modified-Since": "Thu, 01 Jan 2026 00:00:00 GMT",
            },
        )
        self.assertEqual(
            _forwarded(request),
            {
                "if-none-match": "v1",
                "if-modified-since": "Thu, 01 Jan 2026 00:00:00 GMT",
            },
        )

    def test_a_form_post_says_it_is_a_form(self):
        request = Request(URL, data=b"branch=9", method="POST")
        self.assertEqual(
            _forwarded(request), {"content-type": "application/x-www-form-urlencoded"}
        )


if __name__ == "__main__":
    unittest.main()
