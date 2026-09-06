"""The configuration a TOML file describes, as immutable typed records.

Each record mirrors one concern in the file, so a consumer can depend on the
narrow slice it actually needs: cost estimation takes economics, the fetcher
takes acquisition, and only the CLI assembles the whole application config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

DEFAULT_USER_AGENT_ENV = "AUCTION_LENS_HTTP_USER_AGENT"
DEFAULT_CACHE_FILE = "private/cache/provider-response.html"
DEFAULT_LEDGER_FILE = "private/poll-ledger.json"

ASK = "ask"
ALLOW = "allow"
REJECT = "reject"
LARGE_ITEM_POLICIES = frozenset({ASK, ALLOW, REJECT})

PRODUCTION = "production"
DEVELOPMENT = "development"
RUN_MODES = frozenset({PRODUCTION, DEVELOPMENT})

SSL = "ssl"
STARTTLS = "starttls"
EMAIL_SECURITY_MODES = frozenset({SSL, STARTTLS})


@dataclass(frozen=True)
class ProviderConfig:
    """Which auction site a configuration file describes."""

    provider_id: str
    display_name: str = ""
    enabled: bool = False


@dataclass(frozen=True)
class EconomicsConfig:
    """The fees that turn a winning bid into the amount actually paid."""

    buyer_premium_rate: Decimal = Decimal("0")
    premium_is_taxable: bool = True
    sales_tax_rate: Decimal = Decimal("0")
    processing_fee: Decimal = Decimal("0")


@dataclass(frozen=True)
class AcquisitionConfig:
    """How, and how often, the fetcher may contact the provider."""

    mode: str = "manual"
    url: str = ""
    user_agent_env: str = DEFAULT_USER_AGENT_ENV
    timezone: str = "UTC"
    max_requests_per_day: int = 1
    minimum_interval_minutes: int = 720
    timeout_seconds: int = 30
    cache_file: str = DEFAULT_CACHE_FILE
    ledger_file: str = DEFAULT_LEDGER_FILE
    run_mode: str = "production"
    development_minimum_interval_seconds: int = 2


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


@dataclass(frozen=True)
class ValuationConfig:
    enabled: bool = False
    currency: str = "USD"
    sources: tuple[ValuationSourceConfig, ...] = ()


@dataclass(frozen=True)
class LogisticsConfig:
    """Coarse thresholds that decide when handling becomes a question."""

    large_item_policy: str = "ask"
    manual_handling_limit_lb: Decimal = Decimal("75")
    large_dimension_threshold_in: Decimal = Decimal("60")


@dataclass(frozen=True)
class ScoringConfig:
    anomaly_minimum_retail: Decimal = Decimal("100")
    anomaly_maximum_ratio: Decimal = Decimal("0.20")
    minimum_report_score: int = 70
    ending_soon_minutes: int = 20
    condition_penalties: dict[str, int] = field(default_factory=dict)
    rejected_conditions: frozenset[str] = frozenset()
    anomaly_condition: ConditionPolicy = field(default_factory=ConditionPolicy)


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool = False
    host_env: str = "AUCTION_LENS_SMTP_HOST"
    port: int = 465
    security: str = "ssl"
    username_env: str = "AUCTION_LENS_SMTP_USERNAME"
    password_env: str = "AUCTION_LENS_SMTP_PASSWORD"
    sender_env: str = "AUCTION_LENS_EMAIL_FROM"
    recipient_env: str = "AUCTION_LENS_EMAIL_TO"
    subject: str = "Auction Lens report"


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
