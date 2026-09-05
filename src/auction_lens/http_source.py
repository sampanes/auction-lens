from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .config import ProviderConfig


@dataclass(frozen=True)
class FetchResult:
    status: int
    cache_path: Path
    bytes_received: int
    reused_cache: bool


def fetch_authorized_page(
    config: ProviderConfig,
    *,
    now: datetime | None = None,
    opener: Callable = urlopen,
) -> FetchResult:
    """Fetch one authorized public page with persistent limits and conditional caching."""
    if not config.enabled:
        raise RuntimeError("provider is disabled")
    if config.acquisition_mode != "authorized_http":
        raise RuntimeError("provider must use authorized_http acquisition mode")
    _validate_public_url(config.url)
    user_agent = os.getenv(config.user_agent_env, "").strip()
    if "@" not in user_agent:
        raise RuntimeError(f"{config.user_agent_env} must contain the authorized contact email")
    if config.run_mode not in {"development", "production"}:
        raise RuntimeError("run_mode must be 'development' or 'production'")
    if config.run_mode == "production":
        if config.max_requests_per_day < 1:
            raise RuntimeError("max_requests_per_day must be at least 1")

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    ledger_path = Path(config.ledger_file)
    ledger = _read_json(ledger_path, default={"attempts": []})
    attempts = [_parse_datetime(value) for value in ledger.get("attempts", [])]
    local_zone = ZoneInfo(config.timezone)
    local_date = instant.astimezone(local_zone).date()
    today = [value for value in attempts if value.astimezone(local_zone).date() == local_date]
    if config.run_mode == "production":
        if len(today) >= config.max_requests_per_day:
            raise RuntimeError(f"daily request limit reached for {local_date}")
        minimum_interval = timedelta(minutes=config.minimum_interval_minutes)
    else:
        minimum_interval = timedelta(seconds=config.development_minimum_interval_seconds)
    if attempts and instant - max(attempts) < minimum_interval:
        raise RuntimeError(f"{config.run_mode} minimum interval has not elapsed")

    cache_path = Path(config.cache_file)
    metadata_path = cache_path.with_suffix(cache_path.suffix + ".metadata.json")
    metadata = _read_json(metadata_path, default={})
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
    }
    if metadata.get("etag"):
        headers["If-None-Match"] = metadata["etag"]
    if metadata.get("last_modified"):
        headers["If-Modified-Since"] = metadata["last_modified"]

    # Record before opening so failures and interrupted runs cannot cause rapid retries.
    attempts.append(instant.astimezone(timezone.utc))
    _write_json_atomic(ledger_path, {"attempts": [value.isoformat() for value in attempts[-60:]]})
    request = Request(config.url, headers=headers, method="GET")
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            body = response.read()
            status = int(response.status)
            response_headers = response.headers
    except HTTPError as exc:
        if exc.code == 304 and cache_path.exists():
            return FetchResult(304, cache_path, cache_path.stat().st_size, True)
        raise

    if status != 200:
        raise RuntimeError(f"unexpected provider response status {status}")
    _write_bytes_atomic(cache_path, body)
    _write_json_atomic(
        metadata_path,
        {
            "fetched_at": instant.astimezone(timezone.utc).isoformat(),
            "etag": response_headers.get("ETag", ""),
            "last_modified": response_headers.get("Last-Modified", ""),
            "source_url": config.url,
        },
    )
    return FetchResult(status, cache_path, len(body), False)


def _validate_public_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("authorized source URL must be public HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("credentials are not allowed in the source URL")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("ledger timestamps must be timezone-aware")
    return parsed


def _read_json(path: Path, *, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, value: dict) -> None:
    _write_bytes_atomic(path, (json.dumps(value, indent=2) + "\n").encode("utf-8"))


def _write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
