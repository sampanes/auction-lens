"""Reading one TOML file into an :class:`AppConfig`.

Every section of the file gets one small builder, and every builder does exactly
one thing: map keys to fields. It does not check them. The records in ``schema``
enforce their own rules, so the only thing added here is the name of the table
an operator has to open, which a record cannot know.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from ..models import ReadingOrder
from .conditions import resolve_condition_policy
from .schema import (
    DEFAULT_CACHE_FILE,
    DEFAULT_FAR_MINIMUM_SCORE,
    DEFAULT_LEDGER_FILE,
    DEFAULT_SEARCH_CACHE_DIR,
    DEFAULT_USER_AGENT_ENV,
    AcquisitionConfig,
    AcquisitionMode,
    AppConfig,
    EconomicsConfig,
    EmailConfig,
    EmailSecurity,
    InterestDefaults,
    InterestRule,
    LargeItemPolicy,
    LocationPolicy,
    LogisticsConfig,
    ProviderConfig,
    ReportsConfig,
    RunMode,
    ScoringConfig,
    ValuationConfig,
    ValuationSourceConfig,
    WebhookConfig,
)
from .toml_reader import Section, in_section

# Keys consumed by ValuationSourceConfig itself; the rest are adapter settings.
VALUATION_SOURCE_KEYS = frozenset({"id", "adapter", "enabled", "label", "categories", "weight"})


def load_config(path: str | Path) -> AppConfig:
    """Read a provider configuration file and validate it as a whole."""
    with Path(path).open("rb") as handle:
        document = tomllib.load(handle)
    return _app_config(Section(document))


def parse_config(text: str) -> AppConfig:
    """Read configuration text through the same validation as a file."""
    return _app_config(Section(tomllib.loads(text)))


def _app_config(root: Section) -> AppConfig:
    """Build the application record once, whichever boundary supplied TOML."""
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
        webhook=_webhook(root.table("reports").table("webhook")),
        locations=_locations(root.table("locations")),
        reports=_reports(root.table("reports")),
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
            authorization_confirmed=section.flag("authorization_confirmed", False),
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
            category_url_template=section.text("category_url_template"),
            categories=section.texts("categories"),
            max_categories_per_run=section.integer("max_categories_per_run", 12),
            seconds_between_searches=section.decimal("seconds_between_searches", 5),
            session_url=section.text("session_url"),
            session_fields=section.text_map("session_fields"),
            session_change_authorized=section.flag(
                "session_change_authorized", False
            ),
        )


def _scoring(section: Section, conditions: Section, profiles: Section) -> ScoringConfig:
    with in_section(section):
        return ScoringConfig(
            anomaly_minimum_retail=section.decimal("anomaly_minimum_retail", 100),
            anomaly_maximum_ratio=section.decimal("anomaly_maximum_ratio", "0.20"),
            anomaly_weight=section.decimal("anomaly_weight", "0.4"),
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
    """Every rule, already carrying what it inherits.

    Inheritance is resolved here, once, so that everything downstream reads a
    finished rule and nothing has to remember to consult the defaults too.
    """
    defaults = _interest_defaults(root.table("interest_defaults"))
    interests = tuple(
        defaults.applied_to(_interest(item, profiles))
        for item in root.tables("interests")
    )
    _require_unique_interests(interests)
    return interests


def _require_unique_interests(interests: tuple[InterestRule, ...]) -> None:
    """Keep every id-or-name spelling resolvable to exactly one interest."""
    first_by_alias: dict[str, tuple[int, str]] = {}
    for index, interest in enumerate(interests):
        for attribute, key in (("name", "name"), ("interest_id", "id")):
            alias = getattr(interest, attribute).casefold()
            previous = first_by_alias.get(alias)
            if previous is not None and previous[0] != index:
                first, first_key = previous
                raise ValueError(
                    f"interests[{index}].{key} conflicts with "
                    f"interests[{first}].{first_key}; names and ids must identify "
                    "one interest ignoring case"
                )
            if previous is None:
                first_by_alias[alias] = (index, key)


def _interest_defaults(section: Section) -> InterestDefaults:
    with in_section(section):
        return InterestDefaults(
            exclude_terms=section.lowercase_texts("exclude_terms"),
            accessory_nouns=section.lowercase_texts("accessory_nouns"),
        )


def _interest(item: Section, profiles: Section) -> InterestRule:
    with in_section(item):
        wanted = item.optional_positive_integer("wanted")
        if wanted is not None and not item.contains("id"):
            raise ValueError(
                f"{item.label('id')} is required when wanted is set; "
                "finite interests need a stable identity"
            )
        return InterestRule(
            name=item.required_text("name"),
            interest_id=item.required_text("id") if item.contains("id") else "",
            purpose=item.text("purpose", "use"),
            any_terms=item.lowercase_texts("any_terms"),
            all_terms=item.lowercase_texts("all_terms"),
            exclude_terms=item.lowercase_texts("exclude_terms"),
            max_total_cost=item.optional_decimal("max_total_cost"),
            minimum_retail=item.optional_decimal("minimum_retail"),
            wanted=wanted,
            minimum_score=item.integer("minimum_score", 0),
            weight=item.decimal("weight", "1"),
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


def _locations(section: Section) -> LocationPolicy:
    with in_section(section):
        return LocationPolicy(
            allowed=section.lowercase_texts("allowed"),
            far=section.lowercase_texts("far"),
            far_minimum_score=section.integer("far_minimum_score", DEFAULT_FAR_MINIMUM_SCORE),
        )


def _logistics(section: Section) -> LogisticsConfig:
    with in_section(section):
        return LogisticsConfig(
            large_item_policy=section.text("large_item_policy", LargeItemPolicy.ASK),
            manual_handling_limit_lb=section.decimal("manual_handling_limit_lb", 75),
            large_dimension_threshold_in=section.decimal("large_dimension_threshold_in", 60),
            oversized_terms=section.lowercase_texts("oversized_terms"),
        )


def _reports(section: Section) -> ReportsConfig:
    with in_section(section):
        return ReportsConfig(
            max_items=section.optional_positive_integer("max_items"),
            order=section.text("order", ReadingOrder.PRIORITY),
            closing_within_hours=section.optional_positive_integer(
                "closing_within_hours"
            ),
        )


def _webhook(section: Section) -> WebhookConfig:
    with in_section(section):
        return WebhookConfig(
            enabled=section.flag("enabled", False),
            url_env=section.text("url_env", "AUCTION_LENS_WEBHOOK_URL"),
            max_items=section.integer("max_items", 10),
            username=section.text("username", "Auction Lens"),
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
