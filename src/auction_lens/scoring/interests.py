"""Matching a listing against the explicit reasons an operator wants things.

An interest says why an item would be useful, so a match starts from a high
score and is reduced only by conditions that the stated purpose cares about.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import InterestRule
from ..models import Candidate, Listing
from .conditions import penalty_for, policy_admits
from .context import ScoringContext
from .signals import clamp_score

CATEGORY = "wanted"

# An explicitly wanted item is presumed reportable; penalties argue it back down.
BASE_INTEREST_SCORE = 80


def score_interests(
    context: ScoringContext,
    rules: tuple[InterestRule, ...],
    minimum_report_score: int,
) -> list[Candidate]:
    """Return one candidate per interest rule this listing satisfies."""
    matches = []
    for rule in rules:
        candidate = _score_rule(context, rule, minimum_report_score)
        if candidate is not None:
            matches.append(candidate)
    return matches


def _score_rule(
    context: ScoringContext,
    rule: InterestRule,
    minimum_report_score: int,
) -> Candidate | None:
    if not matches_terms(context.listing, context.total_cost, rule):
        return None
    if not policy_admits(context.conditions, rule.condition):
        return None
    penalty = context.baseline_penalty + penalty_for(context.conditions, rule.condition.penalties)
    score = clamp_score(BASE_INTEREST_SCORE + context.bonuses - penalty)
    if score < max(rule.minimum_score, minimum_report_score):
        return None
    reasons = [f"matches {rule.purpose} interest '{rule.name}'"]
    if context.is_ending_soon:
        reasons.append("ending soon")
    return context.candidate(
        category=CATEGORY,
        rule_name=rule.name,
        score=score,
        reasons=reasons,
    )


def matches_terms(listing: Listing, total_cost: Decimal, rule: InterestRule) -> bool:
    """Apply one rule's term filters and its cost ceiling."""
    searchable = _searchable_text(listing)
    if rule.any_terms and not any(term in searchable for term in rule.any_terms):
        return False
    if rule.all_terms and not all(term in searchable for term in rule.all_terms):
        return False
    if any(term in searchable for term in rule.exclude_terms):
        return False
    return rule.max_total_cost is None or total_cost <= rule.max_total_cost


def _searchable_text(listing: Listing) -> str:
    """Terms are matched against what a person reads in the listing headline."""
    return " ".join((listing.title, listing.location, *listing.conditions)).lower()
