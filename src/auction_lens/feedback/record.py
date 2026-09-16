"""Turn a watched item and one short opinion into a private feedback event."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ..watchlist.model import WatchedItem
from .model import (
    FeedbackAction,
    FeedbackEvent,
    FeedbackEvidence,
    FeedbackLabel,
    FeedbackTarget,
    FeedbackTargetKind,
    require_sha256,
)
from .store import FeedbackStore

ANOMALY_TARGET_ID = "price-anomaly"
ANOMALY_TARGET_NAME = "Price anomaly"


def configuration_sha256(path: Path) -> str:
    """Hash the exact configuration bytes a feedback decision was made against."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def infer_feedback_target(
    item: WatchedItem, interest: str | None = None
) -> FeedbackTarget:
    """Resolve a matched interest, requiring a choice only for multi-match lots.

    An explicit selector tries stable id first, then a unique display name. A
    lot with no interest match is feedback about the anomaly rule instead.
    """
    references = item.matched_interests
    if interest is None:
        if not references:
            return FeedbackTarget(
                FeedbackTargetKind.ANOMALY,
                ANOMALY_TARGET_ID,
                ANOMALY_TARGET_NAME,
            )
        if len(references) == 1:
            reference = references[0]
            return FeedbackTarget(
                FeedbackTargetKind.INTEREST,
                reference.interest_id,
                reference.name,
            )
        choices = ", ".join(reference.interest_id for reference in references)
        raise ValueError(
            "this item matched more than one interest; choose one by id or name: "
            + choices
        )

    selector = interest.strip()
    if not selector:
        raise ValueError("interest must be non-empty text")
    by_id = [
        reference
        for reference in references
        if reference.interest_id.casefold() == selector.casefold()
    ]
    matches = by_id or [
        reference
        for reference in references
        if reference.name.casefold() == selector.casefold()
    ]
    if not matches:
        raise ValueError(f"item did not match an interest named {interest!r}")
    if len(matches) > 1:
        raise ValueError(f"interest name {interest!r} is ambiguous; use its stable id")
    reference = matches[0]
    return FeedbackTarget(
        FeedbackTargetKind.INTEREST,
        reference.interest_id,
        reference.name,
    )


def evidence_from_watched_item(item: WatchedItem) -> FeedbackEvidence:
    """Copy decision facts, deliberately omitting links, notes, and profile data."""
    latest = item.latest
    return FeedbackEvidence(
        item_key=item.item_key,
        source=item.source,
        listing_id=item.listing_id,
        inventory_id=item.inventory_id,
        title=item.title,
        observed_at=None if latest is None else latest.scanned_at,
        all_in_cost=None if latest is None else latest.total_cost,
        estimated_retail=item.estimated_retail,
        condition_labels=tuple(
            f"{condition.axis}: {condition.label}" for condition in item.conditions
        ),
        quality_rating=item.quality_rating,
    )


def record_feedback(
    store: FeedbackStore,
    item: WatchedItem,
    label: FeedbackLabel | str,
    config_sha256: str,
    *,
    interest: str | None = None,
    note: str = "",
    recorded_at: datetime | None = None,
) -> FeedbackEvent | None:
    """Append a decision and return it, or return ``None`` for a semantic repeat."""
    event = _event(
        item,
        action=FeedbackAction.RECORD,
        label=FeedbackLabel(label),
        config_sha256=config_sha256,
        interest=interest,
        note=note,
        recorded_at=recorded_at,
    )
    return event if store.append(event) else None


def clear_feedback(
    store: FeedbackStore,
    item: WatchedItem,
    config_sha256: str,
    *,
    interest: str | None = None,
    recorded_at: datetime | None = None,
) -> FeedbackEvent | None:
    """Append a tombstone for current feedback; an already-clear target is a no-op."""
    target = infer_feedback_target(item, interest)
    key = (item.item_key, *target.key)
    if not any(event.current_key == key for event in store.current()):
        return None
    event = _event(
        item,
        action=FeedbackAction.CLEAR,
        label=None,
        config_sha256=config_sha256,
        interest=interest,
        recorded_at=recorded_at,
    )
    return event if store.append(event) else None


def _event(
    item: WatchedItem,
    *,
    action: FeedbackAction,
    label: FeedbackLabel | None,
    config_sha256: str,
    interest: str | None,
    note: str = "",
    recorded_at: datetime | None,
) -> FeedbackEvent:
    target = infer_feedback_target(item, interest)
    evidence = evidence_from_watched_item(item)
    timestamp = datetime.now(UTC) if recorded_at is None else recorded_at
    digest = require_sha256(config_sha256)
    event_id = _event_id(
        recorded_at=timestamp,
        action=action,
        target=target,
        label=label,
        note=note,
        config_sha256=digest,
        evidence=evidence,
    )
    return FeedbackEvent(
        event_id=event_id,
        recorded_at=timestamp,
        action=action,
        item_key=item.item_key,
        target=target,
        label=label,
        note=note,
        config_sha256=digest,
        evidence=evidence,
    )


def _event_id(
    *,
    recorded_at: datetime,
    action: FeedbackAction,
    target: FeedbackTarget,
    label: FeedbackLabel | None,
    note: str,
    config_sha256: str,
    evidence: FeedbackEvidence,
) -> str:
    snapshot = {
        "recorded_at": recorded_at.isoformat(),
        "action": action.value,
        "item_key": evidence.item_key,
        "target": [target.kind.value, target.target_id, target.name],
        "label": None if label is None else label.value,
        "note": note,
        "config_sha256": config_sha256,
        "evidence": {
            "source": evidence.source,
            "listing_id": evidence.listing_id,
            "inventory_id": evidence.inventory_id,
            "title": evidence.title,
            "observed_at": (
                None if evidence.observed_at is None else evidence.observed_at.isoformat()
            ),
            "all_in_cost": (
                None if evidence.all_in_cost is None else str(evidence.all_in_cost)
            ),
            "estimated_retail": (
                None
                if evidence.estimated_retail is None
                else str(evidence.estimated_retail)
            ),
            "condition_labels": list(evidence.condition_labels),
            "quality_rating": evidence.quality_rating,
        },
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return "feedback-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
