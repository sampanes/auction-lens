"""Fetching authorized public pages, with caching and request limits.

Every guard here exists so that an unattended scheduled run cannot become a
burden on a provider: the request identifies its operator, is counted before it
is made, and revalidates the cached copy instead of re-downloading it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request
from zoneinfo import ZoneInfo

from ..config.provider import AcquisitionConfig, AcquisitionMode, ProviderConfig, RunMode
from ..files import read_json, write_bytes_atomically, write_json_atomically
from ..http_safety import public_https_opener, require_public_https

ACCEPTED_CONTENT = "text/html,application/xhtml+xml"

HTTP_OK = 200
HTTP_NOT_MODIFIED = 304
HTTP_TOO_MANY_REQUESTS = 429

CONTACT_ADDRESS = re.compile(
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+",
    re.IGNORECASE,
)
PLACEHOLDER_CONTACT_DOMAINS = frozenset(
    {"example.com", "example.net", "example.org", "localhost"}
)
PLACEHOLDER_CONTACT_SUFFIXES = (".example", ".invalid", ".localhost", ".test")

METADATA_SUFFIX = ".metadata.json"
ETAG = "etag"
LAST_MODIFIED = "last_modified"
FETCHED_AT = "fetched_at"

# Enough request history to explain today's decisions without growing forever.
RETAINED_ATTEMPTS = 60
ATTEMPTS_KEY = "attempts"


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
    opener: Callable | None = None,
) -> FetchResult:
    """Fetch the configured page, or explain why this run must not."""
    require_fetch_allowed(provider, config, config.url)
    user_agent = authorized_user_agent(config)
    instant = _timezone_aware(now or datetime.now(UTC))
    opener = public_https_opener() if opener is None else opener

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
    if config.authorization_confirmed is not True:
        raise RuntimeError(
            "[provider.acquisition] authorization_confirmed = true is required "
            "before contacting the provider"
        )
    require_public_https(url)


def authorized_user_agent(config: AcquisitionConfig) -> str:
    """The provider must be able to tell who is making the request."""
    user_agent = os.getenv(config.user_agent_env, "").strip()
    addresses = CONTACT_ADDRESS.findall(user_agent)
    if not addresses:
        raise RuntimeError(
            f"{config.user_agent_env} must contain the authorized operator's "
            "contact email"
        )
    if all(_placeholder_contact(address) for address in addresses):
        raise RuntimeError(
            f"{config.user_agent_env} must contain the authorized operator's "
            "contact email, not an example or placeholder address"
        )
    return user_agent


def _placeholder_contact(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    registered_example = any(
        domain == placeholder or domain.endswith(f".{placeholder}")
        for placeholder in PLACEHOLDER_CONTACT_DOMAINS
    )
    return registered_example or domain.endswith(PLACEHOLDER_CONTACT_SUFFIXES)


def _timezone_aware(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return instant


@dataclass(frozen=True)
class ResponseCache:
    """A cached response body and the metadata needed to revalidate it."""

    path: Path

    @classmethod
    def at(cls, path: str | Path) -> ResponseCache:
        return cls(Path(path))

    @property
    def metadata_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + METADATA_SUFFIX)

    def exists(self) -> bool:
        return self.path.exists()

    def size(self) -> int:
        return self.path.stat().st_size

    def fetched_at(self) -> datetime | None:
        """Return when this body was downloaded, when metadata records it.

        A reused body is as old as its download, not as old as the run that
        reused it. Everything read from the body was true at this moment and
        no other, so this is the honest date to put on those listings.
        """
        recorded = read_json(self.metadata_path, default={}).get(FETCHED_AT, "")
        return datetime.fromisoformat(recorded) if recorded else None

    def conditional_headers(self) -> dict[str, str]:
        """Ask the provider to send a body only if the cached copy is stale."""
        metadata = read_json(self.metadata_path, default={})
        headers = {}
        if metadata.get(ETAG):
            headers["If-None-Match"] = metadata[ETAG]
        if metadata.get(LAST_MODIFIED):
            headers["If-Modified-Since"] = metadata[LAST_MODIFIED]
        return headers

    def store(
        self,
        body: bytes,
        *,
        headers: Mapping[str, str],
        fetched_at: datetime,
        source_url: str,
    ) -> None:
        """Replace the cached body, then record what can revalidate it."""
        write_bytes_atomically(self.path, body)
        write_json_atomically(
            self.metadata_path,
            {
                FETCHED_AT: fetched_at.isoformat(),
                ETAG: headers.get("ETag", ""),
                LAST_MODIFIED: headers.get("Last-Modified", ""),
                "source_url": source_url,
            },
        )


@dataclass(frozen=True)
class PollLedger:
    """Previous request attempts, persisted so restarts still respect limits."""

    path: Path

    @classmethod
    def at(cls, path: str | Path) -> PollLedger:
        return cls(Path(path))

    def attempts(self) -> list[datetime]:
        stored = read_json(self.path, default={}).get(ATTEMPTS_KEY, [])
        return [_parse_attempt(value) for value in stored]

    def record(self, instant: datetime) -> None:
        """Append one attempt, keeping only the most recent entries."""
        attempts = [*self.attempts(), instant.astimezone(UTC)]
        write_json_atomically(
            self.path,
            {ATTEMPTS_KEY: [value.isoformat() for value in attempts[-RETAINED_ATTEMPTS:]]},
        )


def enforce_request_limits(
    attempts: list[datetime],
    config: AcquisitionConfig,
    instant: datetime,
) -> None:
    """Raise unless another request is allowed right now.

    Production counts requests against the provider's calendar day. Development
    only spaces requests out, so parser work stays polite without a daily quota.
    """
    if config.run_mode == RunMode.PRODUCTION:
        _enforce_daily_limit(attempts, config, instant)
    if attempts and instant - max(attempts) < _minimum_interval(config):
        raise RuntimeError(f"{config.run_mode} minimum interval has not elapsed")


def _enforce_daily_limit(
    attempts: list[datetime],
    config: AcquisitionConfig,
    instant: datetime,
) -> None:
    zone = config.zone
    today = _local_date(instant, zone)
    used = [value for value in attempts if _local_date(value, zone) == today]
    if len(used) >= config.max_requests_per_day:
        raise RuntimeError(f"daily request limit reached for {today}")


def _minimum_interval(config: AcquisitionConfig) -> timedelta:
    if config.run_mode == RunMode.PRODUCTION:
        return timedelta(minutes=config.minimum_interval_minutes)
    return timedelta(seconds=config.development_minimum_interval_seconds)


def _local_date(instant: datetime, zone: ZoneInfo) -> date:
    return instant.astimezone(zone).date()


def _parse_attempt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("ledger timestamps must be timezone-aware")
    return parsed
