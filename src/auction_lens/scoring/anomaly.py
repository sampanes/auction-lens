"""Discovery of listings that are cheap relative to their stated retail value.

This path exists to surface things no interest rule mentions. Provider-stated
retail is a ranking signal, not verified market value, so the bar is a ratio
plus a floor: a large discount on a trivial item is not news.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import ScoringConfig
from ..models import Candidate
from .conditions import penalty_for, policy_admits
from .context import ScoringContext
from .signals import HIGHEST_SCORE, clamp_score

CATEGORY = "anomaly"
RULE_NAME = "retail-ratio"


def score_retail_anomaly(context: ScoringContext, scoring: ScoringConfig) -> Candidate | None:
    """Return a candidate when the estimated total is a small share of retail."""
    if not policy_admits(context.conditions, scoring.anomaly_condition):
        return None
    retail = context.listing.estimated_retail
    if retail is None or retail <= 0 or retail < scoring.anomaly_minimum_retail:
        return None
    ratio = context.total_cost / retail
    if ratio > scoring.anomaly_maximum_ratio:
        return None

    penalty = context.baseline_penalty + penalty_for(
        context.conditions, scoring.anomaly_condition.penalties
    )
    score = clamp_score(_discount_score(ratio) + context.bonuses - penalty)
    if score < scoring.minimum_report_score:
        return None
    reasons = [f"estimated total is {ratio:.1%} of stated retail"]
    if context.is_ending_soon:
        reasons.append("ending soon")
    return context.candidate(
        category=CATEGORY,
        rule_name=RULE_NAME,
        score=score,
        reasons=reasons,
    )


def _discount_score(ratio: Decimal) -> int:
    """The deeper the discount, the higher the starting score."""
    return int((Decimal("1") - ratio) * HIGHEST_SCORE)
