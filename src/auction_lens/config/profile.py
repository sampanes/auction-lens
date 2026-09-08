"""A plain-language readback of the operator choices in one configuration.

The TOML remains the only authority. This module receives the already-resolved
records and explains them; it does not read the source file, infer preferences,
or write an alternative representation that could drift from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

from .schema import AppConfig, ConditionPolicy, InterestRule, LargeItemPolicy

NONE = "none"
NO_LIMIT = "no limit"


def render_profile(config: AppConfig) -> str:
    """Describe the stable selection policy without exposing operational data."""
    provider = config.provider.display_name or config.provider.provider_id
    lines = [f"Auction Lens profile for {provider}"]
    _section(lines, "INTERESTS", _interests(config))
    _section(lines, "GENERAL BARGAIN RULE", _general_discovery(config))
    _section(lines, "LOCATIONS", _locations(config))
    _section(lines, "HANDLING", _handling(config))
    _section(lines, "REPORT", _report(config))
    _section(lines, "TEMPORARY CIRCUMSTANCES", _temporary_circumstances())
    return "\n".join(lines).rstrip() + "\n"


def _section(lines: list[str], heading: str, contents: list[str]) -> None:
    lines.extend(("", heading, *contents))


def _interests(config: AppConfig) -> list[str]:
    if not config.interests:
        return ["- No interests configured; only the general bargain rule can match."]

    lines = []
    for number, rule in enumerate(config.interests, start=1):
        lines.extend(_interest(number, rule, config.scoring.minimum_report_score))
    return lines


def _interest(number: int, rule: InterestRule, global_minimum_score: int) -> list[str]:
    threshold = max(rule.minimum_score, global_minimum_score)
    policy_name = f" ({rule.condition_profile})" if rule.condition_profile else ""
    return [
        f"{number}. {rule.name} - purpose: {rule.purpose}",
        f"   Match any: {_terms(rule.any_terms, empty='not required')}",
        f"   Match all: {_terms(rule.all_terms, empty='not required')}",
        f"   Exclude: {_terms(rule.exclude_terms, empty=NONE)}",
        f"   Maximum total cost: {_optional_money(rule.max_total_cost, empty=NO_LIMIT)}",
        f"   Minimum stated retail: {_optional_money(rule.minimum_retail, empty=NONE)}",
        f"   Minimum score: {threshold}; relative importance: {_number(rule.weight)}",
        f"   Conditions{policy_name}: {_condition(rule.condition)}",
    ]


def _general_discovery(config: AppConfig) -> list[str]:
    scoring = config.scoring
    return [
        "- General bargains: stated retail at least "
        f"{_money(scoring.anomaly_minimum_retail)}, total cost at most "
        f"{scoring.anomaly_maximum_ratio:.0%} of it.",
        f"- Minimum score: {scoring.minimum_report_score}; relative importance: "
        f"{_number(scoring.anomaly_weight)}.",
        f"- Ending soon means within {scoring.ending_soon_minutes} minutes.",
        "- Conditions for every purpose: "
        f"{_reject_and_penalties(scoring.rejected_conditions, scoring.condition_penalties)}.",
        f"- Additional bargain conditions: {_condition(scoring.anomaly_condition)}.",
    ]


def _locations(config: AppConfig) -> list[str]:
    allowed = _terms(config.locations.allowed, empty="any pickup location")
    far = _terms(config.locations.far, empty=NONE)
    return [
        f"- Allowed: {allowed}.",
        f"- Far locations (minimum score {config.locations.far_minimum_score}): {far}.",
    ]


def _handling(config: AppConfig) -> list[str]:
    logistics = config.logistics
    policy = {
        LargeItemPolicy.ASK: "ask for a handling plan",
        LargeItemPolicy.ALLOW: "allow without a handling question",
        LargeItemPolicy.REJECT: "reject",
    }[logistics.large_item_policy]
    return [
        f"- Large items: {policy}.",
        f"- A published weight over {_number(logistics.manual_handling_limit_lb)} lb "
        "counts as large.",
        f"- A published dimension over {_number(logistics.large_dimension_threshold_in)} in "
        "counts as large.",
        f"- Title, condition, or pickup-location phrases that count as large: "
        f"{_terms(logistics.oversized_terms, empty=NONE)}.",
    ]


def _report(config: AppConfig) -> list[str]:
    maximum = config.reports.max_items
    length = "all matches" if maximum is None else f"the best {maximum} matches"
    valuation = config.valuation
    if not valuation.enabled:
        valuation_summary = "off"
    else:
        active = sum(source.enabled for source in valuation.sources)
        valuation_summary = (
            f"on in {valuation.currency}; {active} of {len(valuation.sources)} sources active"
        )
    return [f"- Length: {length}.", f"- Valuation: {valuation_summary}."]


def _temporary_circumstances() -> list[str]:
    return [
        "- None are stored in this profile.",
        "- Use daily --visiting BRANCH when a normally far branch is already on today's route.",
    ]


def _condition(policy: ConditionPolicy) -> str:
    unknown = "unknown accepted" if policy.allow_unknown else "unknown rejected"
    return f"{unknown}; {_reject_and_penalties(policy.reject, policy.penalties)}"


def _reject_and_penalties(
    rejected: Iterable[str], penalties: Mapping[str, int]
) -> str:
    rejected_text = _terms(sorted(rejected), empty=NONE)
    penalty_text = ", ".join(
        _penalty(label, amount)
        for label, amount in sorted(penalties.items())
    ) or NONE
    return f"reject {rejected_text}; penalties {penalty_text}"


def _penalty(label: str, amount: int) -> str:
    effect = f"-{amount}" if amount else "0"
    return f"{label} ({effect})"


def _terms(values: Iterable[str], *, empty: str) -> str:
    return ", ".join(_quoted(value) for value in values) or empty


def _quoted(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _optional_money(value: Decimal | None, *, empty: str) -> str:
    return empty if value is None else _money(value)


def _money(value: Decimal) -> str:
    return f"${value:.2f}"


def _number(value: Decimal) -> str:
    written = format(value, "f")
    return written.rstrip("0").rstrip(".") if "." in written else written
