"""Keeping a valuation source from being called too often within one run.

The acquisition fetcher persists its limits because it runs once a day; this
one is in-memory on purpose, because it bounds a single fan-out over listings.

The numbers arrive already checked, from ``RequestLimits`` in ``settings``.
This is the mechanism, not the rule.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class RequestThrottle:
    """A per-run request budget and a minimum gap between requests."""

    max_requests: int
    minimum_interval_seconds: float
    monotonic: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    _requests_made: int = field(default=0, init=False)
    _last_request_at: float | None = field(default=None, init=False)

    def take_turn(self) -> None:
        """Wait if a request is due later, and refuse once the budget is spent."""
        if self._requests_made >= self.max_requests:
            raise RuntimeError(f"max_requests_per_run ({self.max_requests}) reached")
        if self._last_request_at is not None:
            remaining = self.minimum_interval_seconds - (
                self.monotonic() - self._last_request_at
            )
            if remaining > 0:
                self.sleeper(remaining)
        self._last_request_at = self.monotonic()
        self._requests_made += 1
