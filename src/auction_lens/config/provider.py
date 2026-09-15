"""Which provider may be contacted, how, and how often."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..values import require_at_least, require_not_negative, settle_choice

DEFAULT_USER_AGENT_ENV = "AUCTION_LENS_HTTP_USER_AGENT"
DEFAULT_CACHE_FILE = "private/cache/provider-response.html"
DEFAULT_SEARCH_CACHE_DIR = "private/cache/searches"
DEFAULT_LEDGER_FILE = "private/poll-ledger.json"

# Where a search term is written into the search address.
QUERY_PLACEHOLDER = "{query}"
CATEGORY_PLACEHOLDER = "{category}"


class RunMode(StrEnum):
    """Whether a run is the real daily poll or someone iterating on a parser."""

    PRODUCTION = "production"
    DEVELOPMENT = "development"


class AcquisitionMode(StrEnum):
    """Where listing data comes from."""

    MANUAL = "manual"
    AUTHORIZED_HTTP = "authorized_http"


@dataclass(frozen=True)
class ProviderConfig:
    """Which auction site a configuration file describes."""

    provider_id: str
    display_name: str = ""
    enabled: bool = False


@dataclass(frozen=True)
class AcquisitionConfig:
    """How, and how often, the fetcher may contact the provider."""

    mode: AcquisitionMode = AcquisitionMode.MANUAL
    # Choosing authorized_http describes the transport. This separate switch
    # records the operator's affirmative decision that they actually have
    # permission to use it; copied public configuration must fail closed.
    authorization_confirmed: bool = False
    url: str = ""
    user_agent_env: str = DEFAULT_USER_AGENT_ENV
    timezone: str = "UTC"
    max_requests_per_day: int = 1
    minimum_interval_minutes: int = 720
    timeout_seconds: int = 30
    cache_file: str = DEFAULT_CACHE_FILE
    ledger_file: str = DEFAULT_LEDGER_FILE
    run_mode: RunMode = RunMode.PRODUCTION
    development_minimum_interval_seconds: int = 2
    search_url_template: str = ""
    searches: tuple[str, ...] = ()
    search_cache_dir: str = DEFAULT_SEARCH_CACHE_DIR
    max_searches_per_run: int = 8
    # A sweep answers what a search term cannot: what is here that I would
    # want but would never have thought to type. Capped separately from the
    # searches so a long list of terms can never starve it.
    category_url_template: str = ""
    categories: tuple[str, ...] = ()
    max_categories_per_run: int = 12
    seconds_between_searches: Decimal = Decimal("5")
    session_url: str = ""
    session_fields: dict[str, str] = field(default_factory=dict)
    # A POST that establishes branch-scoping session state can fall outside
    # permission to poll public pages, so it needs its own confirmation.
    session_change_authorized: bool = False

    @property
    def zone(self) -> ZoneInfo:
        """The provider's clock used for closing times and request quotas."""
        return ZoneInfo(self.timezone)

    def __post_init__(self) -> None:
        settle_choice(self, "mode", AcquisitionMode)
        settle_choice(self, "run_mode", RunMode)
        require_at_least(self.max_requests_per_day, 1, field_name="max_requests_per_day")
        require_at_least(self.timeout_seconds, 1, field_name="timeout_seconds")
        require_not_negative(
            self.minimum_interval_minutes, field_name="minimum_interval_minutes"
        )
        require_not_negative(
            self.development_minimum_interval_seconds,
            field_name="development_minimum_interval_seconds",
        )
        require_at_least(self.max_searches_per_run, 1, field_name="max_searches_per_run")
        require_at_least(
            self.max_categories_per_run, 1, field_name="max_categories_per_run"
        )
        require_not_negative(
            self.seconds_between_searches, field_name="seconds_between_searches"
        )
        if self.search_url_template and QUERY_PLACEHOLDER not in self.search_url_template:
            raise ValueError(
                "search_url_template must contain {QUERY_PLACEHOLDER}"
            )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA time zone") from error
