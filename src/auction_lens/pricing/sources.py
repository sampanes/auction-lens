"""The small contract and shared configuration for every price source.

A source answers one question about one listing. This file also holds the
shared URL-template and request-limit rules used by the concrete adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from urllib.parse import quote_plus

from ..config.pricing import ValuationSourceConfig
from ..config.toml import Section, in_section
from ..listings.model import Listing
from ..values import require_at_least, require_not_negative
from .model import ResearchLink, ValuationObservation

SOURCES_PATH = "valuation.sources"

DEFAULT_CACHE_DIR = "private/valuation-cache"
DEFAULT_CACHE_HOURS = 24
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_REQUESTS_PER_RUN = 20
DEFAULT_MINIMUM_INTERVAL_SECONDS = 1

PLACEHOLDERS = ("query", "brand", "model", "category")


@dataclass(frozen=True)
class SourceResult:
    """What one source produced for one listing."""

    observations: tuple[ValuationObservation, ...] = ()
    research_links: tuple[ResearchLink, ...] = ()


class ValuationAdapter(Protocol):
    """The one-method contract implemented by every price source."""

    def collect(self, listing: Listing) -> SourceResult: ...


def settings_of(config: ValuationSourceConfig) -> Section:
    """Name one source's settings like the rest of the operator's config."""
    return Section(config.settings, f"{SOURCES_PATH}.{config.source_id}")


@dataclass(frozen=True)
class RequestLimits:
    """Limits that keep an automated source polite to someone else's server."""

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
    """Read shared network settings; the record validates unsafe values."""
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


def research_query(listing: Listing) -> str:
    """Prefer structured identity, falling back to the provider's whole title."""
    if listing.brand and listing.model:
        return f"{listing.brand} {listing.model}"
    return listing.title


def fill_template(template: str, listing: Listing) -> str:
    """Fill the four documented, URL-encoded listing placeholders."""
    values = {
        "query": research_query(listing),
        "brand": listing.brand,
        "model": listing.model,
        "category": listing.category,
    }
    filled = template
    for name in PLACEHOLDERS:
        filled = filled.replace("{" + name + "}", quote_plus(values[name]))
    return filled
