from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlsplit
from urllib.request import Request, urlopen

from ..config import ValuationSourceConfig
from ..models import Listing, ValuationObservation, money, parse_datetime
from .base import SourceResult


class HttpJsonAdapter:
    """Map a read-only HTTPS JSON API into valuation observations declaratively."""

    def __init__(
        self,
        config: ValuationSourceConfig,
        *,
        opener: Callable[..., Any] = urlopen,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.opener = opener
        self.now = now
        self.monotonic = monotonic
        self.sleeper = sleeper
        self._last_request_at: float | None = None
        self._request_count = 0

    def collect(self, listing: Listing) -> SourceResult:
        endpoint = self._endpoint(listing)
        payload = self._load_payload(endpoint)
        rows = _read_path(payload, str(self.config.settings.get("items_path", "")))
        if not isinstance(rows, list):
            rows = [rows]
        fields = self.config.settings.get("fields", {})
        if not isinstance(fields, dict) or "typical" not in fields:
            raise ValueError("fields.typical is required for an HTTP JSON source")
        observations = tuple(self._observation(row, fields) for row in rows)
        return SourceResult(observations=observations)

    def _endpoint(self, listing: Listing) -> str:
        template = str(self.config.settings.get("endpoint", ""))
        query = " ".join(value for value in (listing.brand, listing.model) if value) or listing.title
        values = {
            "query": query,
            "brand": listing.brand,
            "model": listing.model,
            "category": listing.category,
        }
        endpoint = template
        for name, value in values.items():
            endpoint = endpoint.replace("{" + name + "}", quote_plus(value))
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("HTTP JSON endpoints must be public HTTPS URLs without URL credentials")
        return endpoint

    def _load_payload(self, endpoint: str) -> Any:
        cache_dir = Path(str(self.config.settings.get("cache_dir", "private/valuation-cache")))
        cache_key = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        cache_file = cache_dir / f"{self.config.source_id}-{cache_key}.json"
        cache_hours = Decimal(str(self.config.settings.get("cache_hours", 24)))
        if cache_file.is_file():
            modified = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc)
            if self.now() - modified <= timedelta(hours=float(cache_hours)):
                return json.loads(cache_file.read_text(encoding="utf-8"))

        headers = self._headers()
        maximum = int(self.config.settings.get("max_requests_per_run", 20))
        if self._request_count >= maximum:
            raise RuntimeError(f"max_requests_per_run ({maximum}) reached")
        minimum_interval = float(self.config.settings.get("minimum_interval_seconds", 1))
        if minimum_interval < 0:
            raise ValueError("minimum_interval_seconds cannot be negative")
        if self._last_request_at is not None:
            remaining = minimum_interval - (self.monotonic() - self._last_request_at)
            if remaining > 0:
                self.sleeper(remaining)
        request = Request(endpoint, headers=headers, method="GET")
        timeout = int(self.config.settings.get("timeout_seconds", 20))
        self._last_request_at = self.monotonic()
        self._request_count += 1
        with self.opener(request, timeout=timeout) as response:
            body = response.read()
        payload = json.loads(body.decode("utf-8"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache_file)
        return payload

    def _headers(self) -> dict[str, str]:
        configured = self.config.settings.get("headers", {})
        if not isinstance(configured, dict):
            raise ValueError("headers must be a TOML table")
        headers = {"Accept": "application/json"}
        for name, configured_value in configured.items():
            value = str(configured_value)
            if value.startswith("env:"):
                variable = value.removeprefix("env:")
                value = os.getenv(variable, "")
                if not value:
                    raise RuntimeError(f"required environment variable {variable!r} is empty")
            headers[str(name)] = value
        return headers

    def _observation(self, row: Any, fields: dict[str, Any]) -> ValuationObservation:
        typical = money(_read_path(row, str(fields["typical"])), field_name="typical")
        low = money(_optional_path(row, fields.get("low"), typical), field_name="low")
        high = money(_optional_path(row, fields.get("high"), typical), field_name="high")
        if not low <= typical <= high:
            raise ValueError("HTTP valuation requires low <= typical <= high")
        basis = str(_optional_path(row, fields.get("basis"), self.config.settings.get("basis", "used_sold")))
        currency = str(_optional_path(row, fields.get("currency"), self.config.settings.get("currency", "USD")))
        return ValuationObservation(
            source_id=self.config.source_id,
            basis=basis.lower(),
            low=low,
            typical=typical,
            high=high,
            currency=currency.upper(),
            sample_size=max(1, int(_optional_path(row, fields.get("sample_size"), 1))),
            confidence=Decimal(str(_optional_path(row, fields.get("confidence"), 1))),
            observed_at=parse_datetime(_optional_path(row, fields.get("observed_at"), None)),
            url=str(_optional_path(row, fields.get("url"), "")),
            notes=str(_optional_path(row, fields.get("notes"), "")),
        )


def _optional_path(value: Any, path: Any, default: Any) -> Any:
    return default if path in (None, "") else _read_path(value, str(path))


def _read_path(value: Any, path: str) -> Any:
    """Read a deliberately small dotted path language: results.0.price.value."""
    current = value
    if not path:
        return current
    for part in path.split("."):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current
