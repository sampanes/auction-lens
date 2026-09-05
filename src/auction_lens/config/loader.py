"""Reading one TOML file into an :class:`AppConfig`.

Every section of the file gets one small builder, so a new setting is added
next to the settings it belongs with rather than inside a growing function.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .conditions import resolve_condition_policy
from .schema import (
    DEFAULT_CACHE_FILE,
    DEFAULT_LEDGER_FILE,
    DEFAULT_USER_AGENT_ENV,
    EMAIL_SECURITY_MODES,
    LARGE_ITEM_POLICIES,
    AcquisitionConfig,
    AppConfig,
    EconomicsConfig,
    EmailConfig,
    InterestRule,
    LogisticsConfig,
    ProviderConfig,
    ScoringConfig,
    ValuationConfig,
    ValuationSourceConfig,
)
from .toml_reader import Section

SOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# Keys consumed by ValuationSourceConfig itself; the rest are adapter settings.
VALUATION_SOURCE_KEYS = frozenset({"id", "adapter", "enabled", "label", "categories", "weight"})


def load_config(path: str | Path) -> AppConfig:
    """Read a provider configuration file and validate it as a whole."""
    with Path(path).open("rb") as handle:
        root = Section(tomllib.load(handle))
    if root.contains("wanted"):
        raise ValueError("[[wanted]] was renamed to [[interests]]; rename the tables")

    profiles = root.table("condition_profiles")
    provider = root.table("provider")
    return AppConfig(
        provider=_provider(provider),
        economics=_economics(root.table("economics")),
        acquisition=_acquisition(provider.table("acquisition")),
        scoring=_scoring(root.table("scoring"), root.table("conditions"), profiles),
        interests=_interests(root, profiles),
        valuation=_valuation(root.table("valuation")),
        logistics=_logistics(root.table("logistics")),
        email=_email(root.table("reports").table("email")),
        allowed_locations=root.table("locations").lowercase_texts("allowed"),
    )


def _provider(section: Section) -> ProviderConfig:
    return ProviderConfig(
        provider_id=section.text("id", "unknown"),
        display_name=section.text("display_name"),
        enabled=section.flag("enabled", False),
    )


def _economics(section: Section) -> EconomicsConfig:
    return EconomicsConfig(
        buyer_premium_rate=section.non_negative_decimal("default_buyer_premium", 0),
        premium_is_taxable=section.flag("premium_is_taxable", True),
        sales_tax_rate=section.non_negative_decimal("sales_tax_rate", 0),
        processing_fee=section.non_negative_decimal("processing_fee", 0),
    )


def _acquisition(section: Section) -> AcquisitionConfig:
    return AcquisitionConfig(
        mode=section.text("mode", "manual"),
        url=section.text("url"),
        user_agent_env=section.text("user_agent_env", DEFAULT_USER_AGENT_ENV),
        timezone=section.text("timezone", "UTC"),
        max_requests_per_day=section.integer("max_requests_per_day", 1),
        minimum_interval_minutes=section.integer("minimum_interval_minutes", 720),
        timeout_seconds=section.integer("timeout_seconds", 30),
        cache_file=section.text("cache_file", DEFAULT_CACHE_FILE),
        ledger_file=section.text("ledger_file", DEFAULT_LEDGER_FILE),
        run_mode=section.text("run_mode", "production").lower(),
        development_minimum_interval_seconds=section.integer(
            "development_minimum_interval_seconds", 2
        ),
    )


def _scoring(section: Section, conditions: Section, profiles: Section) -> ScoringConfig:
    return ScoringConfig(
        anomaly_minimum_retail=section.non_negative_decimal("anomaly_minimum_retail", 100),
        anomaly_maximum_ratio=section.non_negative_decimal("anomaly_maximum_ratio", "0.20"),
        minimum_report_score=section.integer("minimum_report_score", 70),
        ending_soon_minutes=section.integer("ending_soon_minutes", 20),
        condition_penalties=conditions.integer_map("penalties"),
        rejected_conditions=frozenset(conditions.lowercase_texts("reject")),
        anomaly_condition=resolve_condition_policy(
            section,
            profiles,
            profile_key="anomaly_condition_profile",
            inline_key="anomaly_condition",
        ),
    )


def _interests(root: Section, profiles: Section) -> tuple[InterestRule, ...]:
    return tuple(
        InterestRule(
            name=item.required_text("name"),
            purpose=item.text("purpose", "use"),
            any_terms=item.lowercase_texts("any_terms"),
            all_terms=item.lowercase_texts("all_terms"),
            exclude_terms=item.lowercase_texts("exclude_terms"),
            max_total_cost=item.optional_decimal("max_total_cost"),
            minimum_score=item.integer("minimum_score", 0),
            condition_profile=item.text("condition_profile"),
            condition=resolve_condition_policy(item, profiles),
        )
        for item in root.tables("interests")
    )


def _valuation(section: Section) -> ValuationConfig:
    sources = tuple(_valuation_source(item) for item in section.tables("sources"))
    _reject_unusable_sources(sources)
    return ValuationConfig(
        enabled=section.flag("enabled", False),
        currency=section.text("currency", "USD").upper(),
        sources=sources,
    )


def _valuation_source(item: Section) -> ValuationSourceConfig:
    source_id = item.required_text("id")
    return ValuationSourceConfig(
        source_id=source_id,
        adapter=item.required_text("adapter"),
        enabled=item.flag("enabled", True),
        label=item.text("label", source_id),
        categories=item.lowercase_texts("categories"),
        weight=item.decimal("weight", 1),
        settings={
            key: value for key, value in item.data.items() if key not in VALUATION_SOURCE_KEYS
        },
    )


def _reject_unusable_sources(sources: tuple[ValuationSourceConfig, ...]) -> None:
    """Source ids name cache files and report rows, so they stay safe and unique."""
    identifiers = [source.source_id for source in sources]
    unsafe = [value for value in identifiers if not SOURCE_ID_PATTERN.fullmatch(value)]
    if unsafe:
        raise ValueError(f"invalid valuation source id: {unsafe[0]!r}")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("valuation source ids must be unique")
    if any(source.weight <= 0 for source in sources):
        raise ValueError("valuation source weights must be greater than zero")


def _logistics(section: Section) -> LogisticsConfig:
    config = LogisticsConfig(
        large_item_policy=section.text("large_item_policy", "ask").lower(),
        manual_handling_limit_lb=section.non_negative_decimal("manual_handling_limit_lb", 75),
        large_dimension_threshold_in=section.non_negative_decimal(
            "large_dimension_threshold_in", 60
        ),
    )
    if config.large_item_policy not in LARGE_ITEM_POLICIES:
        raise ValueError("large_item_policy must be ask, allow, or reject")
    return config


def _email(section: Section) -> EmailConfig:
    config = EmailConfig(
        enabled=section.flag("enabled", False),
        host_env=section.text("host_env", "AUCTION_LENS_SMTP_HOST"),
        port=section.integer("port", 465),
        security=section.text("security", "ssl").lower(),
        username_env=section.text("username_env", "AUCTION_LENS_SMTP_USERNAME"),
        password_env=section.text("password_env", "AUCTION_LENS_SMTP_PASSWORD"),
        sender_env=section.text("sender_env", "AUCTION_LENS_EMAIL_FROM"),
        recipient_env=section.text("recipient_env", "AUCTION_LENS_EMAIL_TO"),
        subject=section.text("subject", "Auction Lens report"),
    )
    if config.security not in EMAIL_SECURITY_MODES:
        raise ValueError("email security must be 'ssl' or 'starttls'")
    return config
