from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .config import AppConfig, InterestRule
from .logistics import assess_logistics
from .models import Candidate, Listing, LogisticsDecision, ObservationChange


def estimate_total_cost(listing: Listing, config: AppConfig) -> Decimal:
    premium_rate = listing.buyer_premium_rate
    if premium_rate is None:
        premium_rate = config.provider.buyer_premium_rate
    premium = listing.current_bid * premium_rate
    taxable = listing.current_bid + (premium if config.provider.premium_is_taxable else Decimal("0"))
    tax = taxable * config.provider.sales_tax_rate
    return (listing.current_bid + premium + tax + config.provider.processing_fee).quantize(Decimal("0.01"))


def evaluate(
    listing: Listing,
    config: AppConfig,
    change: ObservationChange | None = None,
    *,
    logistics_decision: LogisticsDecision | None = None,
    now: datetime | None = None,
) -> list[Candidate]:
    change = change or ObservationChange(is_new=True, price_changed=False)
    if config.allowed_locations and not any(
        location in listing.location.lower() for location in config.allowed_locations
    ):
        return []
    logistics = assess_logistics(listing, config.logistics, logistics_decision)
    if logistics.status == "infeasible":
        return []
    total = estimate_total_cost(listing, config) + logistics.added_cost
    normalized_conditions = set(listing.conditions)
    if normalized_conditions & config.scoring.rejected_conditions:
        return []

    global_penalty = sum(
        config.scoring.condition_penalties.get(value, 0) for value in normalized_conditions
    )
    urgency = _urgency_bonus(listing, config, now or datetime.now(timezone.utc))
    change_bonus = 3 if change.is_new else 2 if change.price_changed else 0
    candidates: list[Candidate] = []

    for rule in config.interests:
        if _matches_interest(listing, total, rule):
            if normalized_conditions & rule.condition.reject:
                continue
            if not normalized_conditions and not rule.condition.allow_unknown:
                continue
            penalty = global_penalty + sum(
                rule.condition.penalties.get(value, 0) for value in normalized_conditions
            )
            score = max(0, min(100, 80 + urgency + change_bonus - penalty))
            if score >= max(rule.minimum_score, config.scoring.minimum_report_score):
                reasons = [f"matches {rule.purpose} interest '{rule.name}'"]
                if urgency:
                    reasons.append("ending soon")
                candidates.append(
                    _candidate(listing, "wanted", rule.name, score, total, reasons, change, logistics)
                )

    retail = listing.estimated_retail
    anomaly_condition = config.scoring.anomaly_condition
    anomaly_allowed = not (normalized_conditions & anomaly_condition.reject)
    anomaly_allowed = anomaly_allowed and bool(
        normalized_conditions or anomaly_condition.allow_unknown
    )
    if anomaly_allowed and retail and retail >= config.scoring.anomaly_minimum_retail and retail > 0:
        ratio = total / retail
        if ratio <= config.scoring.anomaly_maximum_ratio:
            base = int((Decimal("1") - ratio) * 100)
            anomaly_penalty = global_penalty + sum(
                anomaly_condition.penalties.get(value, 0) for value in normalized_conditions
            )
            score = max(0, min(100, base + urgency + change_bonus - anomaly_penalty))
            if score >= config.scoring.minimum_report_score:
                reasons = [f"estimated total is {ratio:.1%} of stated retail"]
                if urgency:
                    reasons.append("ending soon")
                candidates.append(
                    _candidate(
                        listing,
                        "anomaly",
                        "retail-ratio",
                        score,
                        total,
                        reasons,
                        change,
                        logistics,
                    )
                )
    return candidates


def _matches_interest(listing: Listing, total: Decimal, rule: InterestRule) -> bool:
    haystack = " ".join((listing.title, listing.location, *listing.conditions)).lower()
    if rule.any_terms and not any(term in haystack for term in rule.any_terms):
        return False
    if rule.all_terms and not all(term in haystack for term in rule.all_terms):
        return False
    if rule.exclude_terms and any(term in haystack for term in rule.exclude_terms):
        return False
    return rule.max_total_cost is None or total <= rule.max_total_cost


def _urgency_bonus(listing: Listing, config: AppConfig, now: datetime) -> int:
    if listing.ends_at is None:
        return 0
    minutes = (listing.ends_at - now).total_seconds() / 60
    return 7 if 0 <= minutes <= config.scoring.ending_soon_minutes else 0


def _candidate(listing, category, rule_name, score, total, reasons, change, logistics):
    ratio = total / listing.estimated_retail if listing.estimated_retail else None
    return Candidate(
        listing=listing,
        category=category,
        rule_name=rule_name,
        score=score,
        total_cost=total,
        retail_ratio=ratio,
        reasons=tuple(reasons),
        change=change,
        logistics=logistics,
    )
