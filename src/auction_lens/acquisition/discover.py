"""Finding lots by asking the provider's own search, once per term.

A search page lists a whole page of lots and carries the data for all of them,
so one request describes forty lots. Asking for each lot's own page instead
would be forty requests for information already received, which is most of the
difference between a polite client and a nuisance.

Two limits apply here and they answer different questions. The persistent ledger
in ``polling`` answers "may this run happen at all", and counts one attempt for
the whole run. The in-memory throttle answers "how fast may this run work", and
spaces the searches inside it. Conflating the two would either forbid a second
search for twelve hours or let one run fire every search at once.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from ..config import AcquisitionConfig, ProviderConfig
from ..config.schema import QUERY_PLACEHOLDER
from ..throttle import RequestThrottle
from .cache import ResponseCache
from .fetch import (
    ACCEPTED_CONTENT,
    HTTP_NOT_MODIFIED,
    HTTP_OK,
    authorized_user_agent,
    require_fetch_allowed,
)
from .polling import PollLedger, enforce_request_limits

# Enough digest to keep two similar terms in separate files, short enough that a
# person can still see at a glance which file belongs to which search.
DIGEST_LENGTH = 8


@dataclass(frozen=True)
class SearchCapture:
    """One search page as it was fetched, and where it was saved."""

    term: str
    url: str
    path: Path
    reused_cache: bool


def discover_searches(
    provider: ProviderConfig,
    config: AcquisitionConfig,
    terms: Iterable[str],
    *,
    now: datetime | None = None,
    opener: Callable = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[SearchCapture]:
    """Fetch one search page per term, or explain why this run must not."""
    if not config.search_url_template:
        raise RuntimeError("provider.acquisition.search_url_template is not configured")
    wanted = _terms_to_ask(terms, config)
    instant = _timezone_aware(now or datetime.now(UTC))

    addresses = [(term, _address(config, term)) for term in wanted]
    # Check every address before making any request, so a run that is going to
    # be refused is refused before it has touched the provider at all.
    for _, url in addresses:
        require_fetch_allowed(provider, config, url)
    user_agent = authorized_user_agent(config)

    ledger = PollLedger.at(config.ledger_file)
    enforce_request_limits(ledger.attempts(), config, instant)
    # One discovery run is one attempt however many searches it makes. Recorded
    # before the first connection, so an interrupted run still counts.
    ledger.record(instant)

    throttle = RequestThrottle(
        max_requests=len(addresses),
        minimum_interval_seconds=float(config.seconds_between_searches),
        sleeper=sleeper,
    )
    return [
        _fetch_one(term, url, config, user_agent, instant, opener, throttle)
        for term, url in addresses
    ]


def _terms_to_ask(terms: Iterable[str], config: AcquisitionConfig) -> list[str]:
    """Take each distinct term once, in the order given, up to the run's cap."""
    seen: dict[str, None] = {}
    for term in terms:
        cleaned = term.strip().lower()
        if cleaned:
            seen.setdefault(cleaned, None)
    if not seen:
        raise RuntimeError("no search terms; configure searches or pass --search")
    return list(seen)[: config.max_searches_per_run]


def _address(config: AcquisitionConfig, term: str) -> str:
    """Write the term into the configured search address."""
    return config.search_url_template.replace(QUERY_PLACEHOLDER, quote_plus(term))


def cache_path_for(config: AcquisitionConfig, term: str, url: str) -> Path:
    """Name a term's cached page readably, and uniquely for its exact address."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
    return Path(config.search_cache_dir) / f"{_slug(term)}-{digest}.html"


def _slug(term: str) -> str:
    """A filename a person can recognise, made from a phrase they typed."""
    kept = "".join(letter if letter.isalnum() else "-" for letter in term.lower())
    return kept.strip("-") or "search"


def _fetch_one(
    term: str,
    url: str,
    config: AcquisitionConfig,
    user_agent: str,
    instant: datetime,
    opener: Callable,
    throttle: RequestThrottle,
) -> SearchCapture:
    """Revalidate this term's cached page, downloading only if it has changed."""
    cache = ResponseCache.at(cache_path_for(config, term, url))
    headers = {
        "User-Agent": user_agent,
        "Accept": ACCEPTED_CONTENT,
        **cache.conditional_headers(),
    }
    throttle.take_turn()
    request = Request(url, headers=headers, method="GET")
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            body = response.read()
            status = int(response.status)
            response_headers = response.headers
    except HTTPError as error:
        if error.code == HTTP_NOT_MODIFIED and cache.exists():
            return SearchCapture(term, url, cache.path, True)
        raise

    if status != HTTP_OK:
        raise RuntimeError(f"unexpected provider response status {status} for {term!r}")
    cache.store(
        body,
        headers=response_headers,
        fetched_at=instant.astimezone(UTC),
        source_url=url,
    )
    return SearchCapture(term, url, cache.path, False)


def _timezone_aware(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return instant
