"""Fetching one authorized public page, politely and reproducibly.

Every guard here exists so that an unattended scheduled run cannot become a
burden on a provider: the request identifies its operator, is counted before it
is made, and revalidates the cached copy instead of re-downloading it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..config import AcquisitionConfig, ProviderConfig
from .cache import ResponseCache
from .polling import RUN_MODES, PRODUCTION, PollLedger, enforce_request_limits

AUTHORIZED_HTTP = "authorized_http"
ACCEPTED_CONTENT = "text/html,application/xhtml+xml"

HTTP_OK = 200
HTTP_NOT_MODIFIED = 304


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
    _require_fetch_allowed(provider, config)
    user_agent = _authorized_user_agent(config)
    instant = _timezone_aware(now or datetime.now(timezone.utc))

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
        raise

    if status != HTTP_OK:
        raise RuntimeError(f"unexpected provider response status {status}")
    cache.store(
        body,
        headers=response_headers,
        fetched_at=instant.astimezone(timezone.utc),
        source_url=config.url,
    )
    return FetchResult(status, cache.path, len(body), False)


def _require_fetch_allowed(provider: ProviderConfig, config: AcquisitionConfig) -> None:
    """Check every precondition for contacting the provider at all."""
    if not provider.enabled:
        raise RuntimeError("provider is disabled")
    if config.mode != AUTHORIZED_HTTP:
        raise RuntimeError("provider must use authorized_http acquisition mode")
    if config.run_mode not in RUN_MODES:
        raise RuntimeError("run_mode must be 'development' or 'production'")
    if config.run_mode == PRODUCTION and config.max_requests_per_day < 1:
        raise RuntimeError("max_requests_per_day must be at least 1")
    _require_public_https(config.url)


def _authorized_user_agent(config: AcquisitionConfig) -> str:
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
