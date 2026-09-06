"""The configuration a TOML file describes, as immutable typed records.

Each record mirrors one concern in the file, so a consumer can depend on the
narrow slice it actually needs: cost estimation takes economics, the fetcher
takes acquisition, and only the CLI assembles the whole application config.

A record enforces its own rules in ``__post_init__``. That is what lets every
consumer downstream simply use a value instead of re-checking it, and it is why
the loader in this package is nothing but field mapping. Where a setting may
only be one of a few words, it is an enum, so a typo is caught by construction
rather than by a membership test repeated at each use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..fields import (
    require_at_least,
    require_at_most,
    require_finite,
    require_not_negative,
    require_within,
)
from ..models import HIGHEST_SCORE, LOWEST_SCORE

DEFAULT_USER_AGENT_ENV = "AUCTION_LENS_HTTP_USER_AGENT"
DEFAULT_CACHE_FILE = "private/cache/provider-response.html"
DEFAULT_LEDGER_FILE = "private/poll-ledger.json"

HIGHEST_PORT = 65535

# A rate is a proportion, so anything above 1 is a misplaced percentage.
HIGHEST_RATE = Decimal("1")

# Source ids name cache files and report rows, so they stay filesystem-safe.
SOURCE_ID_CHARACTERS = "letters, digits, and _ . -"

Choice = TypeVar("Choice", bound=StrEnum)


class LargeItemPolicy(StrEnum):
    """What to do about a lot too heavy or too bulky to carry casually."""

    ASK = "ask"
    ALLOW = "allow"
    REJECT = "reject"


class RunMode(StrEnum):
    """Whether a run is the real daily poll or someone iterating on a parser."""

    PRODUCTION = "production"
    DEVELOPMENT = "development"


class EmailSecurity(StrEnum):
    """How the SMTP connection is protected."""

    SSL = "ssl"
    STARTTLS = "starttls"


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
class EconomicsConfig:
    """The fees that turn a winning bid into the amount actually paid."""

    default_buyer_premium: Decimal = Decimal("0")
    premium_is_taxable: bool = True
    sales_tax_rate: Decimal = Decimal("0")
    processing_fee: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        _require_rate(self.default_buyer_premium, field_name="default_buyer_premium")
        _require_rate(self.sales_tax_rate, field_name="sales_tax_rate")
        require_not_negative(self.processing_fee, field_name="processing_fee")


@dataclass(frozen=True)
class AcquisitionConfig:
    """How, and how often, the fetcher may contact the provider."""

    mode: AcquisitionMode = AcquisitionMode.MANUAL
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

    def __post_init__(self) -> None:
        _settle(self, "mode", AcquisitionMode)
        _settle(self, "run_mode", RunMode)
        require_at_least(self.max_requests_per_day, 1, field_name="max_requests_per_day")
        require_at_least(self.timeout_seconds, 1, field_name="timeout_seconds")
        require_not_negative(
            self.minimum_interval_minutes, field_name="minimum_interval_minutes"
        )
        require_not_negative(
            self.development_minimum_interval_seconds,
            field_name="development_minimum_interval_seconds",
        )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA time zone") from error


@dataclass(frozen=True)
class ConditionPolicy:
    """Condition rules scoped to one interest, and therefore to one intended use."""

    reject: frozenset[str] = frozenset()
    penalties: dict[str, int] = field(default_factory=dict)
    allow_unknown: bool = True


@dataclass(frozen=True)
class InterestRule:
    """One reason a listing would be useful, with the conditions that use allows."""

    name: str
    purpose: str = "use"
    any_terms: tuple[str, ...] = ()
    all_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    max_total_cost: Decimal | None = None
    minimum_score: int = 0
    condition_profile: str = ""
    condition: ConditionPolicy = field(default_factory=ConditionPolicy)

    def __post_init__(self) -> None:
        require_within(
            self.minimum_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="minimum_score",
        )
        if self.max_total_cost is not None:
            require_not_negative(self.max_total_cost, field_name="max_total_cost")


@dataclass(frozen=True)
class ValuationSourceConfig:
    """One configured source instance consumed by a reusable adapter."""

    source_id: str
    adapter: str
    enabled: bool = True
    label: str = ""
    categories: tuple[str, ...] = ()
    weight: Decimal = Decimal("1")
    settings: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_safe_identifier(self.source_id):
            raise ValueError(
                f"id {self.source_id!r} may contain only {SOURCE_ID_CHARACTERS}, "
                "and must start with a letter or digit"
            )
        require_finite(self.weight, field_name="weight")
        if self.weight <= 0:
            raise ValueError("weight must be greater than zero")


@dataclass(frozen=True)
class ValuationConfig:
    """Every price source, and the one currency their answers are read in."""

    enabled: bool = False
    currency: str = "USD"
    sources: tuple[ValuationSourceConfig, ...] = ()

    def __post_init__(self) -> None:
        identifiers = [source.source_id for source in self.sources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("source ids must be unique")


@dataclass(frozen=True)
class LogisticsConfig:
    """Coarse thresholds that decide when handling becomes a question."""

    large_item_policy: LargeItemPolicy = LargeItemPolicy.ASK
    manual_handling_limit_lb: Decimal = Decimal("75")
    large_dimension_threshold_in: Decimal = Decimal("60")

    def __post_init__(self) -> None:
        _settle(self, "large_item_policy", LargeItemPolicy)
        require_not_negative(
            self.manual_handling_limit_lb, field_name="manual_handling_limit_lb"
        )
        require_not_negative(
            self.large_dimension_threshold_in, field_name="large_dimension_threshold_in"
        )


@dataclass(frozen=True)
class ScoringConfig:
    """The bar a listing has to clear, and what counts against it."""

    anomaly_minimum_retail: Decimal = Decimal("100")
    anomaly_maximum_ratio: Decimal = Decimal("0.20")
    minimum_report_score: int = 70
    ending_soon_minutes: int = 20
    condition_penalties: dict[str, int] = field(default_factory=dict)
    rejected_conditions: frozenset[str] = frozenset()
    anomaly_condition: ConditionPolicy = field(default_factory=ConditionPolicy)

    def __post_init__(self) -> None:
        require_within(
            self.minimum_report_score,
            low=LOWEST_SCORE,
            high=HIGHEST_SCORE,
            field_name="minimum_report_score",
        )
        require_not_negative(
            self.anomaly_minimum_retail, field_name="anomaly_minimum_retail"
        )
        _require_rate(self.anomaly_maximum_ratio, field_name="anomaly_maximum_ratio")
        require_not_negative(self.ending_soon_minutes, field_name="ending_soon_minutes")


@dataclass(frozen=True)
class EmailConfig:
    """Where a report is sent, and which variables hold the secrets."""

    enabled: bool = False
    host_env: str = "AUCTION_LENS_SMTP_HOST"
    port: int = 465
    security: EmailSecurity = EmailSecurity.SSL
    username_env: str = "AUCTION_LENS_SMTP_USERNAME"
    password_env: str = "AUCTION_LENS_SMTP_PASSWORD"
    sender_env: str = "AUCTION_LENS_EMAIL_FROM"
    recipient_env: str = "AUCTION_LENS_EMAIL_TO"
    subject: str = "Auction Lens report"

    def __post_init__(self) -> None:
        _settle(self, "security", EmailSecurity)
        require_within(self.port, low=1, high=HIGHEST_PORT, field_name="port")


@dataclass(frozen=True)
class AppConfig:
    """Everything one configuration file declares, ready for the pipeline."""

    provider: ProviderConfig
    economics: EconomicsConfig
    acquisition: AcquisitionConfig
    scoring: ScoringConfig
    interests: tuple[InterestRule, ...]
    valuation: ValuationConfig
    logistics: LogisticsConfig
    email: EmailConfig
    allowed_locations: tuple[str, ...] = ()


def _settle(record: Any, field_name: str, options: type[Choice]) -> None:
    """Replace a settings word with the member it names, on a frozen record.

    TOML hands us "ssl"; a test or a future caller may hand us the member. Both
    arrive here and leave as the member, so no consumer downstream has to wonder
    which it got. Frozen records are written through ``object.__setattr__``
    because ``__post_init__`` runs after the field has already been assigned.
    """
    written = getattr(record, field_name)
    try:
        settled = options(str(written).strip().lower())
    except ValueError as error:
        allowed = ", ".join(option.value for option in options)
        raise ValueError(f"{field_name} must be one of: {allowed}") from error
    object.__setattr__(record, field_name, settled)


def _require_rate(value: Decimal, *, field_name: str) -> None:
    """A rate is a proportion: 0.15 means 15 percent, and 15 is a misplaced percentage."""
    require_not_negative(value, field_name=field_name)
    require_at_most(value, HIGHEST_RATE, field_name=field_name)


def _is_safe_identifier(value: str) -> bool:
    """Accept a name that is safe in a file path and readable in a report."""
    if not value or not value[0].isalnum() or not value.isascii():
        return False
    return all(character.isalnum() or character in "_.-" for character in value)
