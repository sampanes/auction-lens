"""A read-only JSON API described entirely in configuration.

Most price APIs differ only in their URL and in where the numbers sit in the
response, so this adapter takes both as settings. That keeps a new authorized
source a TOML edit rather than a new Python module.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..config import ValuationSourceConfig
from ..fields import parse_money, parse_utc_datetime
from ..models import Listing, ValuationObservation
from .base import SourceResult
from .http_cache import JsonResponseCache
from .json_path import read_optional_path, read_path
from .templates import fill_template
from .throttle import RequestThrottle

DEFAULT_CACHE_DIR = "private/valuation-cache"
DEFAULT_CACHE_HOURS = 24
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_REQUESTS_PER_RUN = 20
DEFAULT_MINIMUM_INTERVAL_SECONDS = 1

ENVIRONMENT_PREFIX = "env:"
REQUIRED_FIELD = "typical"

# This adapter is the only one that contacts a third party, so the operator has
# to state in configuration that the source permits this use.
AUTHORIZATION_SETTING = "authorization_confirmed"


class HttpJsonAdapter:
    """Map a read-only HTTPS JSON API into valuation observations declaratively."""

    def __init__(
        self,
        config: ValuationSourceConfig,
        *,
        opener: Callable[..., Any] = urlopen,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        throttle: RequestThrottle | None = None,
    ):
        self.config = config
        self.settings = config.settings
        self.opener = opener
        cache_hours = float(self.settings.get("cache_hours", DEFAULT_CACHE_HOURS))
        self.cache = JsonResponseCache(
            directory=Path(str(self.settings.get("cache_dir", DEFAULT_CACHE_DIR))),
            source_id=config.source_id,
            lifetime=timedelta(hours=cache_hours),
            clock=clock,
        )
        self.throttle = throttle or RequestThrottle(
            max_requests=int(
                self.settings.get("max_requests_per_run", DEFAULT_MAX_REQUESTS_PER_RUN)
            ),
            minimum_interval_seconds=float(
                self.settings.get("minimum_interval_seconds", DEFAULT_MINIMUM_INTERVAL_SECONDS)
            ),
        )

    def collect(self, listing: Listing) -> SourceResult:
        """Query the configured endpoint and map every returned row."""
        if self.settings.get(AUTHORIZATION_SETTING) is not True:
            raise ValueError(
                f"valuation source {self.config.source_id!r} needs "
                f"{AUTHORIZATION_SETTING} = true before it may be queried"
            )
        fields = self.settings.get("fields", {})
        if not isinstance(fields, dict) or REQUIRED_FIELD not in fields:
            raise ValueError("fields.typical is required for an HTTP JSON source")
        payload = self._payload(self._endpoint(listing))
        rows = read_path(payload, str(self.settings.get("items_path", "")))
        if not isinstance(rows, list):
            rows = [rows]
        return SourceResult(observations=tuple(self._observation(row, fields) for row in rows))

    def _endpoint(self, listing: Listing) -> str:
        endpoint = fill_template(str(self.settings.get("endpoint", "")), listing)
        parsed = urlsplit(endpoint)
        is_public = parsed.scheme == "https" and parsed.hostname
        if not is_public or parsed.username or parsed.password:
            raise ValueError(
                "HTTP JSON endpoints must be public HTTPS URLs without URL credentials"
            )
        return endpoint

    def _payload(self, endpoint: str) -> Any:
        """Reuse a fresh cached response; otherwise take a turn and request one."""
        cached = self.cache.read_fresh(endpoint)
        if cached is not None:
            return cached
        headers = self._headers()
        self.throttle.take_turn()
        request = Request(endpoint, headers=headers, method="GET")
        timeout = int(self.settings.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        with self.opener(request, timeout=timeout) as response:
            body = response.read()
        payload = json.loads(body.decode("utf-8"))
        self.cache.store(endpoint, payload)
        return payload

    def _headers(self) -> dict[str, str]:
        """Resolve configured headers, reading env: values from the environment."""
        configured = self.settings.get("headers", {})
        if not isinstance(configured, dict):
            raise ValueError("headers must be a TOML table")
        headers = {"Accept": "application/json"}
        for name, raw_value in configured.items():
            headers[str(name)] = _resolved_header(str(raw_value))
        return headers

    def _observation(self, row: Any, fields: dict[str, Any]) -> ValuationObservation:
        typical = parse_money(read_path(row, str(fields[REQUIRED_FIELD])), field_name="typical")
        low = parse_money(_field(row, fields, "low", typical), field_name="low")
        high = parse_money(_field(row, fields, "high", typical), field_name="high")
        basis = str(_field(row, fields, "basis", self.settings.get("basis", "used_sold")))
        currency = str(_field(row, fields, "currency", self.settings.get("currency", "USD")))
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=basis.lower(),
            low=low,
            typical=typical,
            high=high,
            currency=currency.upper(),
            sample_size=int(_field(row, fields, "sample_size", 1)),
            confidence=Decimal(str(_field(row, fields, "confidence", 1))),
            observed_at=parse_utc_datetime(_field(row, fields, "observed_at", None)),
            url=str(_field(row, fields, "url", "")),
            notes=str(_field(row, fields, "notes", "")),
        )


def _field(row: Any, fields: dict[str, Any], name: str, default: Any) -> Any:
    """Read one mapped field from a row, or fall back to the source default."""
    return read_optional_path(row, fields.get(name), default)


def _resolved_header(value: str) -> str:
    """Read a header value, or the environment variable it names."""
    if not value.startswith(ENVIRONMENT_PREFIX):
        return value
    variable = value[len(ENVIRONMENT_PREFIX) :]
    resolved = os.getenv(variable, "")
    if not resolved:
        raise RuntimeError(f"required environment variable {variable!r} is empty")
    return resolved
