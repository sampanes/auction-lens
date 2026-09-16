"""Find repeated feedback patterns and exact, review-only price proposals."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import ROUND_CEILING, Decimal

from ..config.interests import InterestRule
from ..values import HIGHEST_RATE, require_at_least
from .model import (
    FeedbackEvent,
    FeedbackLabel,
    FeedbackPattern,
    FeedbackProposal,
    FeedbackReview,
    FeedbackTarget,
    FeedbackTargetKind,
    ProposalChange,
    require_sha256,
)
from .store import FeedbackStore

DEFAULT_MINIMUM_DISTINCT_ITEMS = 3
PATTERN_LABELS = (
    FeedbackLabel.WRONG_ITEM,
    FeedbackLabel.NO,
    FeedbackLabel.LOGISTICS_IMPOSSIBLE,
    FeedbackLabel.TOO_EXPENSIVE,
)


def review_feedback(
    store: FeedbackStore,
    interests: tuple[InterestRule, ...],
    minimum_distinct_items: int = DEFAULT_MINIMUM_DISTINCT_ITEMS,
    *,
    config_sha256: str | None = None,
) -> FeedbackReview:
    """Review current opinions; never edit an interest or its configuration file.

    A price proposal needs at least one accepted and one rejected item, at least
    ``minimum_distinct_items`` between them, and a gap with no contradictory
    examples. Other negative labels can only become review patterns.
    """
    if isinstance(minimum_distinct_items, bool) or not isinstance(
        minimum_distinct_items, int
    ):
        raise ValueError("minimum_distinct_items must be a whole number")
    require_at_least(
        minimum_distinct_items,
        1,
        field_name="minimum_distinct_items",
    )
    current_digest = None if config_sha256 is None else require_sha256(config_sha256)
    grouped: dict[tuple[str, str], list[FeedbackEvent]] = defaultdict(list)
    for event in store.current():
        grouped[event.target.key].append(event)

    patterns: list[FeedbackPattern] = []
    proposals: list[FeedbackProposal] = []
    rules = {rule.interest_id.casefold(): rule for rule in interests}
    for target_key in sorted(grouped):
        events = sorted(
            grouped[target_key], key=lambda event: (event.item_key, event.event_id)
        )
        target = _current_target(events[0].target, rules)
        for label in PATTERN_LABELS:
            matching = [event for event in events if event.label == label]
            if len(matching) >= minimum_distinct_items:
                patterns.append(
                    FeedbackPattern(
                        target=target,
                        label=label,
                        distinct_items=len(matching),
                        evidence_ids=tuple(event.event_id for event in matching),
                        summary=(
                            f"{len(matching)} current items for {target.name} "
                            f"were marked {label.value}."
                        ),
                    )
                )

        proposal = _price_proposal(
            events,
            target=target,
            rules=rules,
            minimum_distinct_items=minimum_distinct_items,
            config_sha256=current_digest,
        )
        if proposal is not None:
            proposals.append(proposal)
    return FeedbackReview(patterns=tuple(patterns), proposals=tuple(proposals))


def _price_proposal(
    events: list[FeedbackEvent],
    *,
    target: FeedbackTarget,
    rules: dict[str, InterestRule],
    minimum_distinct_items: int,
    config_sha256: str | None,
) -> FeedbackProposal | None:
    if target.kind != FeedbackTargetKind.INTEREST:
        return None
    rule = rules.get(target.target_id.casefold())
    if rule is None:
        return None
    comparable = (
        events
        if config_sha256 is None
        else [event for event in events if event.config_sha256 == config_sha256]
    )
    accepted = [
        event
        for event in comparable
        if event.label in {FeedbackLabel.YES, FeedbackLabel.MAYBE}
    ]
    rejected = [
        event for event in comparable if event.label == FeedbackLabel.TOO_EXPENSIVE
    ]
    if not accepted or not rejected or len(accepted) + len(rejected) < minimum_distinct_items:
        return None
    digest = config_sha256 or _common_config_hash([*accepted, *rejected])
    if digest is None:
        return None

    changes: list[ProposalChange] = []
    cost_after = _clean_boundary(
        [event.evidence.all_in_cost for event in accepted],
        [event.evidence.all_in_cost for event in rejected],
    )
    if cost_after is not None and (
        rule.max_total_cost is None or cost_after < rule.max_total_cost
    ):
        changes.append(
            ProposalChange("max_total_cost", rule.max_total_cost, cost_after)
        )

    ratio_after = _readable_ratio_boundary(
        [event.evidence.retail_ratio for event in accepted],
        [event.evidence.retail_ratio for event in rejected],
    )
    if (
        ratio_after is not None
        and ratio_after <= HIGHEST_RATE
        and (
            rule.maximum_retail_ratio is None
            or ratio_after < rule.maximum_retail_ratio
        )
    ):
        changes.append(
            ProposalChange(
                "maximum_retail_ratio",
                rule.maximum_retail_ratio,
                ratio_after,
            )
        )
    if not changes:
        return None

    evidence_ids = tuple(
        sorted(event.event_id for event in [*accepted, *rejected])
    )
    proposal_id = _proposal_id(
        target_kind=target.kind.value,
        target_id=target.target_id,
        config_sha256=digest,
        changes=changes,
        evidence_ids=evidence_ids,
    )
    return FeedbackProposal(
        proposal_id=proposal_id,
        target=target,
        config_sha256=digest,
        changes=tuple(changes),
        evidence_ids=evidence_ids,
    )


def _clean_boundary(
    accepted: list[Decimal | None], rejected: list[Decimal | None]
) -> Decimal | None:
    """Return the highest accepted value only when every example is comparable."""
    if any(value is None for value in [*accepted, *rejected]):
        return None
    accepted_values = [value for value in accepted if value is not None]
    rejected_values = [value for value in rejected if value is not None]
    boundary = max(accepted_values)
    return boundary if boundary < min(rejected_values) else None


def _readable_ratio_boundary(
    accepted: list[Decimal | None], rejected: list[Decimal | None]
) -> Decimal | None:
    """Round upward to a human-sized ceiling without excluding accepted evidence."""
    boundary = _clean_boundary(accepted, rejected)
    if boundary is None:
        return None
    rejected_floor = min(value for value in rejected if value is not None)
    for decimal_places in range(2, 5):
        unit = Decimal(1).scaleb(-decimal_places)
        readable = boundary.quantize(unit, rounding=ROUND_CEILING).normalize()
        if readable < rejected_floor:
            return readable
    return None


def _current_target(
    historical: FeedbackTarget, rules: dict[str, InterestRule]
) -> FeedbackTarget:
    """Use today's display name while preserving the stable recorded identity."""
    if historical.kind != FeedbackTargetKind.INTEREST:
        return historical
    rule = rules.get(historical.target_id.casefold())
    if rule is None:
        return historical
    return FeedbackTarget(historical.kind, historical.target_id, rule.name)


def _common_config_hash(events: list[FeedbackEvent]) -> str | None:
    digests = {event.config_sha256 for event in events}
    return next(iter(digests)) if len(digests) == 1 else None


def _proposal_id(
    *,
    target_kind: str,
    target_id: str,
    config_sha256: str,
    changes: list[ProposalChange],
    evidence_ids: tuple[str, ...],
) -> str:
    payload = {
        "version": 1,
        "target": {"kind": target_kind, "id": target_id},
        "config_sha256": config_sha256,
        "changes": [
            {
                "field": change.field,
                "before": None if change.before is None else str(change.before),
                "after": str(change.after),
            }
            for change in changes
        ],
        "evidence_ids": list(evidence_ids),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "proposal-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
