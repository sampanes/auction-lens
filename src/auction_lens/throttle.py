"""Keeping a third party from being called too often within one run.

Two callers need this and they need the same thing. A valuation fan-out asks
many sources about many listings; a discovery run asks for several searches in
a row. Both are one burst inside one run, so both are bounded in memory here.

The persistent poll ledger in ``acquisition`` answers a different question --
how often may a run happen at all -- and counts runs, not the requests inside
one. Keep the two apart: this one forgets everything when the process exits.

The numbers arrive already checked from whichever record owns them. This is
the mechanism, not the rule.
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
