"""Fetching one authorized public page, politely and reproducibly.

Every guard here exists so that an unattended scheduled run cannot become a
burden on a provider: the request identifies its operator, is counted before it
is made, and revalidates the cached copy instead of re-downloading it.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..config import AcquisitionConfig, AcquisitionMode, ProviderConfig
from .cache import ResponseCache
from .polling import PollLedger, enforce_request_limits

ACCEPTED_CONTENT = "text/html,application/xhtml+xml"

HTTP_OK = 200
HTTP_NOT_MODIFIED = 304
HTTP_TOO_MANY_REQUESTS = 429


@dataclass(frozen=True)
class FetchResult:
    status: int
    cache_path: Path
    bytes_received: int
    reused_cache: bool


def fetch_authorized_page(
    provider: ProviderConfig,
    config: AcquisitionConfig,
    *,
    now: datetime | None = None,
    opener: Callable = urlopen,
) -> FetchResult:
    """Fetch the configured page, or explain why this run must not."""
    require_fetch_allowed(provider, config, config.url)
    user_agent = authorized_user_agent(config)
    instant = _timezone_aware(now or datetime.now(UTC))

    ledger = PollLedger.at(config.ledger_file)
    enforce_request_limits(ledger.attempts(), config, instant)

    cache = ResponseCache.at(config.cache_file)
    headers = {
        "User-Agent": user_agent,
        "Accept": ACCEPTED_CONTENT,
        **cache.conditional_headers(),
    }

    # Record before opening the connection so that a failure or an interrupted
    # run still counts as an attempt and cannot turn into a rapid retry loop.
    ledger.record(instant)
    request = Request(config.url, headers=headers, method="GET")
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            body = response.read()
            status = int(response.status)
            response_headers = response.headers
    except HTTPError as error:
        if error.code == HTTP_NOT_MODIFIED and cache.exists():
            return FetchResult(HTTP_NOT_MODIFIED, cache.path, cache.size(), True)
        require_not_rate_limited(error)
        raise

    if status != HTTP_OK:
        raise RuntimeError(f"unexpected provider response status {status}")
    cache.store(
        body,
        headers=response_headers,
        fetched_at=instant.astimezone(UTC),
        source_url=config.url,
    )
    return FetchResult(status, cache.path, len(body), False)


def require_not_rate_limited(error: HTTPError) -> None:
    """Turn a provider's "slow down" into an instruction rather than a traceback.

    A 429 is the provider asking for a pause, and the only correct answer is to
    stop for as long as it asks. Retrying sooner is precisely what turns a
    polite client into a blocked one, so this refuses loudly and says to wait.
    """
    if error.code != HTTP_TOO_MANY_REQUESTS:
        return
    retry_after = (error.headers or {}).get("Retry-After", "")
    pause = f" It asks for {retry_after} seconds." if retry_after else ""
    raise RuntimeError(
        f"provider asked for fewer requests (HTTP 429).{pause}"
        " Wait before running again rather than retrying now."
    ) from error


def require_fetch_allowed(
    provider: ProviderConfig, config: AcquisitionConfig, url: str
) -> None:
    """Check every precondition for contacting the provider at all.

    Public because discovery asks the same questions of a different address, and
    there has to be exactly one place that decides what "allowed" means.
    """
    if not provider.enabled:
        raise RuntimeError("provider is disabled")
    if config.mode != AcquisitionMode.AUTHORIZED_HTTP:
        raise RuntimeError("provider must use authorized_http acquisition mode")
    _require_public_https(url)


def authorized_user_agent(config: AcquisitionConfig) -> str:
    """The provider must be able to tell who is making the request."""
    user_agent = os.getenv(config.user_agent_env, "").strip()
    if "@" not in user_agent:
        raise RuntimeError(f"{config.user_agent_env} must contain the authorized contact email")
    return user_agent


def _require_public_https(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("authorized source URL must be public HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("credentials are not allowed in the source URL")


def _timezone_aware(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return instant
