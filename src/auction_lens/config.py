from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    enabled: bool
    acquisition_mode: str
    authorization_required: bool
    buyer_premium_rate: Decimal
    premium_is_taxable: bool
    sales_tax_rate: Decimal
    processing_fee: Decimal
    url: str = ""
    user_agent_env: str = "AUCTION_LENS_HTTP_USER_AGENT"
    timezone: str = "UTC"
    max_requests_per_day: int = 1
    minimum_interval_minutes: int = 720
    timeout_seconds: int = 30
    cache_file: str = "private/cache/provider-response.html"
    ledger_file: str = "private/poll-ledger.json"
    run_mode: str = "production"
    development_minimum_interval_seconds: int = 2


@dataclass(frozen=True)
class ConditionPolicy:
    """Condition rules scoped to one interest and therefore one intended use."""

    reject: frozenset[str] = frozenset()
    penalties: dict[str, int] = field(default_factory=dict)
    allow_unknown: bool = True


@dataclass(frozen=True)
class InterestRule:
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
    """Coarse thresholds that trigger a contextual handling question."""

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
    provider: ProviderConfig
    scoring: ScoringConfig
    interests: tuple[InterestRule, ...]
    valuation: ValuationConfig
    logistics: LogisticsConfig
    email: EmailConfig
    allowed_locations: tuple[str, ...] = ()

    @property
    def wanted(self) -> tuple[InterestRule, ...]:
        """Compatibility name for configurations created before interests had purposes."""
        return self.interests


def load_config(path: str | Path) -> AppConfig:
    with Path(path).open("rb") as handle:
        raw = tomllib.load(handle)

    provider = raw.get("provider", {})
    economics = raw.get("economics", {})
    acquisition = provider.get("acquisition", {})
    scoring = raw.get("scoring", {})
    condition = raw.get("conditions", {})
    email = raw.get("reports", {}).get("email", {})
    locations = raw.get("locations", {})
    logistics = raw.get("logistics", {})
    condition_profiles = raw.get("condition_profiles", {})

    provider_config = ProviderConfig(
        provider_id=str(provider.get("id", "unknown")),
        enabled=bool(provider.get("enabled", False)),
        acquisition_mode=str(acquisition.get("mode", "manual")),
        authorization_required=bool(acquisition.get("authorization_required", True)),
        buyer_premium_rate=Decimal(str(economics.get("default_buyer_premium", 0))),
        premium_is_taxable=bool(economics.get("premium_is_taxable", True)),
        sales_tax_rate=Decimal(str(economics.get("sales_tax_rate", 0))),
        processing_fee=Decimal(str(economics.get("processing_fee", 0))),
        url=str(acquisition.get("url", "")),
        user_agent_env=str(acquisition.get("user_agent_env", "AUCTION_LENS_HTTP_USER_AGENT")),
        timezone=str(acquisition.get("timezone", "UTC")),
        max_requests_per_day=int(acquisition.get("max_requests_per_day", 1)),
        minimum_interval_minutes=int(acquisition.get("minimum_interval_minutes", 720)),
        timeout_seconds=int(acquisition.get("timeout_seconds", 30)),
        cache_file=str(acquisition.get("cache_file", "private/cache/provider-response.html")),
        ledger_file=str(acquisition.get("ledger_file", "private/poll-ledger.json")),
        run_mode=str(acquisition.get("run_mode", "production")).lower(),
        development_minimum_interval_seconds=int(
            acquisition.get("development_minimum_interval_seconds", 2)
        ),
    )
    scoring_config = ScoringConfig(
        anomaly_minimum_retail=Decimal(str(scoring.get("anomaly_minimum_retail", 100))),
        anomaly_maximum_ratio=Decimal(str(scoring.get("anomaly_maximum_ratio", 0.20))),
        minimum_report_score=int(scoring.get("minimum_report_score", 70)),
        ending_soon_minutes=int(scoring.get("ending_soon_minutes", 20)),
        condition_penalties={str(k).lower(): int(v) for k, v in condition.get("penalties", {}).items()},
        rejected_conditions=frozenset(str(x).lower() for x in condition.get("reject", [])),
        anomaly_condition=_condition_policy(
            scoring,
            condition_profiles,
            profile_key="anomaly_condition_profile",
            inline_key="anomaly_condition",
        ),
    )
    interest_rows = raw.get("interests", raw.get("wanted", []))
    interest_rules = tuple(
        InterestRule(
            name=str(item["name"]),
            purpose=str(item.get("purpose", "use")),
            any_terms=tuple(str(x).lower() for x in item.get("any_terms", [])),
            all_terms=tuple(str(x).lower() for x in item.get("all_terms", [])),
            exclude_terms=tuple(str(x).lower() for x in item.get("exclude_terms", [])),
            max_total_cost=(Decimal(str(item["max_total_cost"])) if "max_total_cost" in item else None),
            minimum_score=int(item.get("minimum_score", 0)),
            condition_profile=str(item.get("condition_profile", "")),
            condition=_condition_policy(item, condition_profiles),
        )
        for item in interest_rows
    )
    valuation = raw.get("valuation", {})
    valuation_sources = tuple(
        ValuationSourceConfig(
            source_id=str(item["id"]),
            adapter=str(item["adapter"]),
            enabled=bool(item.get("enabled", True)),
            label=str(item.get("label", item["id"])),
            categories=tuple(str(x).lower() for x in item.get("categories", [])),
            weight=Decimal(str(item.get("weight", 1))),
            settings={
                str(key): value
                for key, value in item.items()
                if key not in {"id", "adapter", "enabled", "label", "categories", "weight"}
            },
        )
        for item in valuation.get("sources", [])
    )
    source_ids = [source.source_id for source in valuation_sources]
    invalid_ids = [value for value in source_ids if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)]
    if invalid_ids:
        raise ValueError(f"invalid valuation source id: {invalid_ids[0]!r}")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("valuation source ids must be unique")
    if any(source.weight <= 0 for source in valuation_sources):
        raise ValueError("valuation source weights must be greater than zero")
    logistics_config = LogisticsConfig(
        large_item_policy=str(logistics.get("large_item_policy", "ask")).lower(),
        manual_handling_limit_lb=Decimal(
            str(logistics.get("manual_handling_limit_lb", 75))
        ),
        large_dimension_threshold_in=Decimal(
            str(logistics.get("large_dimension_threshold_in", 60))
        ),
    )
    if logistics_config.large_item_policy not in {"ask", "allow", "reject"}:
        raise ValueError("large_item_policy must be ask, allow, or reject")
    if logistics_config.manual_handling_limit_lb < 0:
        raise ValueError("manual_handling_limit_lb cannot be negative")
    if logistics_config.large_dimension_threshold_in < 0:
        raise ValueError("large_dimension_threshold_in cannot be negative")
    email_config = EmailConfig(
        enabled=bool(email.get("enabled", False)),
        host_env=str(email.get("host_env", "AUCTION_LENS_SMTP_HOST")),
        port=int(email.get("port", 465)),
        security=str(email.get("security", "ssl")).lower(),
        username_env=str(email.get("username_env", "AUCTION_LENS_SMTP_USERNAME")),
        password_env=str(email.get("password_env", "AUCTION_LENS_SMTP_PASSWORD")),
        sender_env=str(email.get("sender_env", "AUCTION_LENS_EMAIL_FROM")),
        recipient_env=str(email.get("recipient_env", "AUCTION_LENS_EMAIL_TO")),
        subject=str(email.get("subject", "Auction Lens report")),
    )
    if email_config.security not in {"ssl", "starttls"}:
        raise ValueError("email security must be 'ssl' or 'starttls'")
    return AppConfig(
        provider=provider_config,
        scoring=scoring_config,
        interests=interest_rules,
        valuation=ValuationConfig(
            enabled=bool(valuation.get("enabled", False)),
            currency=str(valuation.get("currency", "USD")).upper(),
            sources=valuation_sources,
        ),
        logistics=logistics_config,
        email=email_config,
        allowed_locations=tuple(str(value).lower() for value in locations.get("allowed", [])),
    )


def _condition_policy(
    owner: dict[str, Any],
    profiles: dict[str, Any],
    *,
    profile_key: str = "condition_profile",
    inline_key: str = "condition",
) -> ConditionPolicy:
    """Resolve a reusable profile, then apply an optional rule-local override."""
    profile_name = str(owner.get(profile_key, ""))
    if profile_name and profile_name not in profiles:
        raise ValueError(f"unknown condition profile {profile_name!r}")
    base = profiles.get(profile_name, {}) if profile_name else {}
    if profile_name and not isinstance(base, dict):
        raise ValueError(f"condition profile {profile_name!r} must be a TOML table")
    inline = owner.get(inline_key, {})
    if not isinstance(inline, dict):
        raise ValueError(f"{inline_key} must be a TOML table")
    reject = inline.get("reject", base.get("reject", []))
    penalties = dict(base.get("penalties", {}))
    penalties.update(inline.get("penalties", {}))
    return ConditionPolicy(
        reject=frozenset(str(value).lower() for value in reject),
        penalties={str(key).lower(): int(value) for key, value in penalties.items()},
        allow_unknown=bool(inline.get("allow_unknown", base.get("allow_unknown", True))),
    )
