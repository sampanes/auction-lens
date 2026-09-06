"""Reading one TOML file into an :class:`AppConfig`.

Every section of the file gets one small builder, and every builder does exactly
one thing: map keys to fields. It does not check them. The records in ``schema``
enforce their own rules, so the only thing added here is the name of the table
an operator has to open, which a record cannot know.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .conditions import resolve_condition_policy
from .schema import (
    DEFAULT_CACHE_FILE,
    DEFAULT_LEDGER_FILE,
    DEFAULT_SEARCH_CACHE_DIR,
    DEFAULT_USER_AGENT_ENV,
    AcquisitionConfig,
    AcquisitionMode,
    AppConfig,
    EconomicsConfig,
    EmailConfig,
    EmailSecurity,
    InterestRule,
    LargeItemPolicy,
    LogisticsConfig,
    ProviderConfig,
    RunMode,
    ScoringConfig,
    ValuationConfig,
    ValuationSourceConfig,
)
from .toml_reader import Section, in_section

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
    with in_section(section):
        return ProviderConfig(
            provider_id=section.text("id", "unknown"),
            display_name=section.text("display_name"),
            enabled=section.flag("enabled", False),
        )


def _economics(section: Section) -> EconomicsConfig:
    with in_section(section):
        return EconomicsConfig(
            default_buyer_premium=section.decimal("default_buyer_premium", 0),
            premium_is_taxable=section.flag("premium_is_taxable", True),
            sales_tax_rate=section.decimal("sales_tax_rate", 0),
            processing_fee=section.decimal("processing_fee", 0),
        )


def _acquisition(section: Section) -> AcquisitionConfig:
    with in_section(section):
        return AcquisitionConfig(
            mode=section.text("mode", AcquisitionMode.MANUAL),
            url=section.text("url"),
            user_agent_env=section.text("user_agent_env", DEFAULT_USER_AGENT_ENV),
            timezone=section.text("timezone", "UTC"),
            max_requests_per_day=section.integer("max_requests_per_day", 1),
            minimum_interval_minutes=section.integer("minimum_interval_minutes", 720),
            timeout_seconds=section.integer("timeout_seconds", 30),
            cache_file=section.text("cache_file", DEFAULT_CACHE_FILE),
            ledger_file=section.text("ledger_file", DEFAULT_LEDGER_FILE),
            run_mode=section.text("run_mode", RunMode.PRODUCTION),
            development_minimum_interval_seconds=section.integer(
                "development_minimum_interval_seconds", 2
            ),
            search_url_template=section.text("search_url_template"),
            searches=section.lowercase_texts("searches"),
            search_cache_dir=section.text("search_cache_dir", DEFAULT_SEARCH_CACHE_DIR),
            max_searches_per_run=section.integer("max_searches_per_run", 8),
            seconds_between_searches=section.decimal("seconds_between_searches", 5),
        )


def _scoring(section: Section, conditions: Section, profiles: Section) -> ScoringConfig:
    with in_section(section):
        return ScoringConfig(
            anomaly_minimum_retail=section.decimal("anomaly_minimum_retail", 100),
            anomaly_maximum_ratio=section.decimal("anomaly_maximum_ratio", "0.20"),
            minimum_report_score=section.integer("minimum_report_score", 70),
            ending_soon_minutes=section.integer("ending_soon_minutes", 20),
            condition_penalties=conditions.non_negative_integer_map("penalties"),
            rejected_conditions=frozenset(conditions.lowercase_texts("reject")),
            anomaly_condition=resolve_condition_policy(
                section,
                profiles,
                profile_key="anomaly_condition_profile",
                inline_key="anomaly_condition",
            ),
        )


def _interests(root: Section, profiles: Section) -> tuple[InterestRule, ...]:
    return tuple(_interest(item, profiles) for item in root.tables("interests"))


def _interest(item: Section, profiles: Section) -> InterestRule:
    with in_section(item):
        return InterestRule(
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


def _valuation(section: Section) -> ValuationConfig:
    with in_section(section):
        return ValuationConfig(
            enabled=section.flag("enabled", False),
            currency=section.text("currency", "USD").upper(),
            sources=tuple(_valuation_source(item) for item in section.tables("sources")),
        )


def _valuation_source(item: Section) -> ValuationSourceConfig:
    with in_section(item):
        source_id = item.required_text("id")
        return ValuationSourceConfig(
            source_id=source_id,
            adapter=item.required_text("adapter"),
            enabled=item.flag("enabled", True),
            label=item.text("label", source_id),
            categories=item.lowercase_texts("categories"),
            weight=item.decimal("weight", 1),
            settings={
                key: value
                for key, value in item.data.items()
                if key not in VALUATION_SOURCE_KEYS
            },
        )


def _logistics(section: Section) -> LogisticsConfig:
    with in_section(section):
        return LogisticsConfig(
            large_item_policy=section.text("large_item_policy", LargeItemPolicy.ASK),
            manual_handling_limit_lb=section.decimal("manual_handling_limit_lb", 75),
            large_dimension_threshold_in=section.decimal("large_dimension_threshold_in", 60),
        )


def _email(section: Section) -> EmailConfig:
    with in_section(section):
        return EmailConfig(
            enabled=section.flag("enabled", False),
            host_env=section.text("host_env", "AUCTION_LENS_SMTP_HOST"),
            port=section.integer("port", 465),
            security=section.text("security", EmailSecurity.SSL),
            username_env=section.text("username_env", "AUCTION_LENS_SMTP_USERNAME"),
            password_env=section.text("password_env", "AUCTION_LENS_SMTP_PASSWORD"),
            sender_env=section.text("sender_env", "AUCTION_LENS_EMAIL_FROM"),
            recipient_env=section.text("recipient_env", "AUCTION_LENS_EMAIL_TO"),
            subject=section.text("subject", "Auction Lens report"),
        )
