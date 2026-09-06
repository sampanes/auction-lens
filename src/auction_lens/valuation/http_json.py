"""A read-only JSON API described entirely in configuration.

Most price APIs differ only in their URL and in where the numbers sit in the
response, so this adapter takes both as settings. That keeps a new authorized
source a TOML edit rather than a new Python module.

Every setting is read through a section reader, so a value written wrongly is
reported against the exact key an operator has to go and fix, in the same words
the rest of the configuration file uses.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..config import Section, ValuationSourceConfig
from ..fields import parse_decimal, parse_money, parse_utc_datetime, parse_whole_number
from ..models import Listing, ValuationObservation
from .base import SourceResult
from .http_cache import JsonResponseCache
from .json_path import read_optional_path, read_path
from .settings import read_request_limits, settings_of
from .templates import fill_template
from .throttle import RequestThrottle

ENVIRONMENT_PREFIX = "env:"
REQUIRED_FIELD = "typical"

DEFAULT_BASIS = "used_sold"
DEFAULT_CURRENCY = "USD"

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
        self.settings = settings_of(config)
        self.limits = read_request_limits(self.settings)
        self.opener = opener
        self.cache = JsonResponseCache(
            directory=Path(self.limits.cache_dir),
            source_id=config.source_id,
            lifetime=timedelta(hours=float(self.limits.cache_hours)),
            clock=clock,
        )
        self.throttle = throttle or RequestThrottle(
            max_requests=self.limits.max_requests_per_run,
            minimum_interval_seconds=float(self.limits.minimum_interval_seconds),
        )

    def collect(self, listing: Listing) -> SourceResult:
        """Query the configured endpoint and map every returned row."""
        if not self.settings.flag(AUTHORIZATION_SETTING, False):
            raise ValueError(
                f"valuation source {self.config.source_id!r} needs "
                f"{AUTHORIZATION_SETTING} = true before it may be queried"
            )
        fields = self.settings.table("fields")
        if not fields.contains(REQUIRED_FIELD):
            raise ValueError(f"{fields.label(REQUIRED_FIELD)} is required")
        payload = self._payload(self._endpoint(listing))
        rows = read_path(payload, self.settings.text("items_path"))
        if not isinstance(rows, list):
            rows = [rows]
        return SourceResult(observations=tuple(self._observation(row, fields) for row in rows))

    def _endpoint(self, listing: Listing) -> str:
        endpoint = fill_template(self.settings.text("endpoint"), listing)
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
        with self.opener(request, timeout=self.limits.timeout_seconds) as response:
            body = response.read()
        payload = json.loads(body.decode("utf-8"))
        self.cache.store(endpoint, payload)
        return payload

    def _headers(self) -> dict[str, str]:
        """Resolve configured headers, reading env: values from the environment."""
        configured = self.settings.table("headers")
        headers = {"Accept": "application/json"}
        for name in configured.data:
            headers[str(name)] = _resolved_header(configured.text(str(name)))
        return headers

    def _observation(self, row: Any, fields: Section) -> ValuationObservation:
        typical = parse_money(
            read_path(row, fields.required_text(REQUIRED_FIELD)), field_name="typical"
        )
        low = parse_money(_field(row, fields, "low", typical), field_name="low")
        high = parse_money(_field(row, fields, "high", typical), field_name="high")
        basis = _field(row, fields, "basis", self.settings.text("basis", DEFAULT_BASIS))
        currency = _field(
            row, fields, "currency", self.settings.text("currency", DEFAULT_CURRENCY)
        )
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=str(basis).lower(),
            low=low,
            typical=typical,
            high=high,
            currency=str(currency).upper(),
            sample_size=parse_whole_number(
                _field(row, fields, "sample_size", 1), field_name="sample_size"
            ),
            confidence=parse_decimal(
                _field(row, fields, "confidence", 1), field_name="confidence"
            ),
            observed_at=parse_utc_datetime(_field(row, fields, "observed_at", None)),
            url=str(_field(row, fields, "url", "")),
            notes=str(_field(row, fields, "notes", "")),
        )


def _field(row: Any, fields: Section, name: str, default: Any) -> Any:
    """Read one mapped field from a row, or fall back to the source default."""
    return read_optional_path(row, fields.text(name), default)


def _resolved_header(value: str) -> str:
    """Read a header value, or the environment variable it names."""
    if not value.startswith(ENVIRONMENT_PREFIX):
        return value
    variable = value[len(ENVIRONMENT_PREFIX) :]
    resolved = os.getenv(variable, "")
    if not resolved:
        raise RuntimeError(f"required environment variable {variable!r} is empty")
    return resolved
