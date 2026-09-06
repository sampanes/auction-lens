"""What an adapter's own settings table is allowed to say.

Every other table in a configuration file has a record in ``config.schema``
that lists its keys and enforces its rules. A source's ``settings`` table
cannot live there, because its keys belong to whichever adapter the source
named. So the same two pieces live here instead: a reader that names the key an
operator has to edit, and a record that decides what the values may be.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..config import Section, ValuationSourceConfig, in_section
from ..fields import require_at_least, require_not_negative

# The dotted path an operator searches their file for. A source is found by its
# id, which is the one thing every configured source is required to write down.
SOURCES_PATH = "valuation.sources"

DEFAULT_CACHE_DIR = "private/valuation-cache"
DEFAULT_CACHE_HOURS = 24
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_REQUESTS_PER_RUN = 20
DEFAULT_MINIMUM_INTERVAL_SECONDS = 1


def settings_of(config: ValuationSourceConfig) -> Section:
    """Read one source's settings the same way the rest of the file is read."""
    return Section(config.settings, f"{SOURCES_PATH}.{config.source_id}")


@dataclass(frozen=True)
class RequestLimits:
    """How hard an adapter may work a third party, and how long it may cache.

    These are the settings that decide whether this project is a good guest on
    someone else's server, so a value that quietly switches one of them off is
    refused rather than obeyed. A cache that never hits and a gap of no time
    both turn one polite run into a burst of traffic.
    """

    cache_dir: str = DEFAULT_CACHE_DIR
    cache_hours: Decimal = Decimal(DEFAULT_CACHE_HOURS)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_requests_per_run: int = DEFAULT_MAX_REQUESTS_PER_RUN
    minimum_interval_seconds: Decimal = Decimal(DEFAULT_MINIMUM_INTERVAL_SECONDS)

    def __post_init__(self) -> None:
        require_not_negative(self.cache_hours, field_name="cache_hours")
        require_at_least(self.timeout_seconds, 1, field_name="timeout_seconds")
        require_at_least(self.max_requests_per_run, 1, field_name="max_requests_per_run")
        require_not_negative(
            self.minimum_interval_seconds, field_name="minimum_interval_seconds"
        )


def read_request_limits(settings: Section) -> RequestLimits:
    """Map settings keys onto the record; the record decides what they may say."""
    with in_section(settings):
        return RequestLimits(
            cache_dir=settings.text("cache_dir", DEFAULT_CACHE_DIR),
            cache_hours=settings.decimal("cache_hours", DEFAULT_CACHE_HOURS),
            timeout_seconds=settings.integer("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_requests_per_run=settings.integer(
                "max_requests_per_run", DEFAULT_MAX_REQUESTS_PER_RUN
            ),
            minimum_interval_seconds=settings.decimal(
                "minimum_interval_seconds", DEFAULT_MINIMUM_INTERVAL_SECONDS
            ),
        )
