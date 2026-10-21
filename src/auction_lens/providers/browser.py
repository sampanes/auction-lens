"""Fetching provider pages through headless Chrome, as a transport and nothing more.

Nellis sits behind an edge firewall that refuses any client that is not a
browser, whatever it sends. Measured on 2026-10-20: urllib was refused, and so
was curl_cffi with Chrome's TLS and HTTP/2 fingerprint and the identifying
User-Agent; headless Chrome with that same User-Agent was admitted. The
provider's written grant, recorded in ignored ``private/``, authorizes exactly
this: browser transport, the same pacing, read-only, and the User-Agent kept.

The browser is used only to make the request. JavaScript is off and every
request the page would make for itself is refused, so one fetch is one document
and the program never runs the provider's scripts, clicks or types. The opener
keeps the urllib shape -- ``opener(request, timeout=...)`` -- so pacing, the
ledger, caching and every caller's error handling stay exactly where they were.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from email.message import Message
from urllib.error import HTTPError, URLError
from urllib.request import Request

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Route, sync_playwright

from ..http_safety import require_public_https, require_same_origin, same_origin

# The installed Chrome, not Playwright's bundled headless shell: it is the
# browser the measurement admitted.
CHANNEL = "chrome"

# The only request headers passed through from the caller. Chrome writes the
# rest itself, which is the point; the User-Agent is the session's own.
FORWARDED_HEADERS = frozenset({"if-none-match", "if-modified-since", "content-type"})
FORM_CONTENT = "application/x-www-form-urlencoded"


def transport(user_agent: str, opener: Callable | None = None) -> AbstractContextManager:
    """The opener a fetch uses: the one injected, or a browser for this run."""
    return browser_opener(user_agent) if opener is None else nullcontext(opener)


@contextmanager
def browser_opener(user_agent: str) -> Iterator[Callable]:
    """Yield an opener that fetches through one headless Chrome session.

    One session per run, so a cookie the provider sets -- the chosen branch --
    holds for every later request, and nothing survives into the next run.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=CHANNEL, headless=True)
        try:
            context = browser.new_context(user_agent=user_agent, java_script_enabled=False)
            yield _Opener(context.new_page())
        finally:
            browser.close()


class _Opener:
    """Make one top-level navigation per call and answer as urllib would."""

    def __init__(self, page):
        self.page = page

    def __call__(self, request: Request, timeout: float) -> BrowserResponse:
        url = request.full_url
        require_public_https(url)
        first = True

        def route(handled: Route) -> None:
            nonlocal first
            asked = handled.request
            # Only the document asked for, or a same-origin redirect from it.
            if (
                not asked.is_navigation_request()
                or asked.frame.parent_frame is not None
                or not same_origin(url, asked.url)
            ):
                handled.abort()
                return
            if not first:
                handled.continue_()
                return
            first = False
            # Headers are replaced only when there is something to add, so the
            # browser's own -- its cookies among them -- go out untouched.
            forwarded = _forwarded(request)
            handled.continue_(
                method=request.get_method(),
                headers={**asked.headers, **forwarded} if forwarded else None,
                post_data=request.data,
            )

        self.page.route("**/*", route)
        try:
            response = self.page.goto(
                url, wait_until="domcontentloaded", timeout=timeout * 1000
            )
            if response is None:
                raise URLError(f"no response for {url}")
            require_same_origin(url, response.url)
            return answer(
                url,
                response.status,
                response.status_text,
                response.headers,
                response.body(),
            )
        except PlaywrightError as error:
            raise URLError(str(error).splitlines()[0]) from error
        finally:
            self.page.unroute("**/*")


def answer(
    url: str, status: int, reason: str, headers: dict[str, str], body: bytes
) -> BrowserResponse:
    """Return a response, or raise the HTTPError urllib would have raised.

    urllib raises for every status outside 2xx, 304 included, and the callers
    were written against that: a 304 is how they learn the cached copy holds.
    """
    message = Message()
    for name, value in headers.items():
        message[name] = value
    if not 200 <= status < 300:
        raise HTTPError(url, status, reason, message, io.BytesIO(body))
    return BrowserResponse(status, message, body)


class BrowserResponse:
    """The small part of an HTTP response the fetchers use."""

    def __init__(self, status: int, headers: Message, body: bytes):
        self.status = status
        self.headers = headers
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> BrowserResponse:
        return self

    def __exit__(self, *_args) -> bool:
        return False


def _forwarded(request: Request) -> dict[str, str]:
    headers = {
        name.lower(): value
        for name, value in request.header_items()
        if name.lower() in FORWARDED_HEADERS
    }
    if request.data is not None:
        headers.setdefault("content-type", FORM_CONTENT)
    return headers

